import asyncio
import socket
import time
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Set, Tuple, Callable, Any, Optional

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class Config:
    """Configuration settings for the Necromoire system"""
    listen_port: int = 1761
    send_port: int = 1762
    host: str = "127.0.0.1"
    buffer_time_ms: int = 18
    input_timeout_ms: int = 50
    num_ranks: int = 8
    rank_size: int = 4
    expected_input_flags: Set[str] = field(default_factory=lambda: {"/uin1", "/uin2"})


@dataclass
class InputState:
    """Manages dual input state from MaxMSP"""
    uin1: str = ""
    uin2: str = ""
    timestamp: float = 0.0
    is_complete: bool = False
    
    def add_input(self, flag: str, data: str) -> bool:
        """Add input data and return True if both inputs received"""
        if flag == "/uin1":
            self.uin1 = data
        elif flag == "/uin2":
            self.uin2 = data
        
        self.timestamp = time.time()
        self.is_complete = bool(self.uin1 and self.uin2)
        return self.is_complete
    
    def get_combined_hex(self) -> str:
        """Combine uin1 and uin2 into single hex string"""
        return self.uin1 + self.uin2
    
    def reset(self) -> None:
        """Reset for next input cycle"""
        self.uin1 = self.uin2 = ""
        self.is_complete = False


@dataclass
class SystemState:
    """Manages the current state of the DBN system"""
    sustain: int = 0
    density_function: int = 0
    key_center: int = 0
    ranks: List[List[int]] = field(default_factory=lambda: [[0]*4 for _ in range(8)])
    
    def update_from_combined_input(self, combined_hex: str) -> None:
        """Parse combined uin1+uin2 hex string and update state"""
        expected_length = 3 + (8 * 4)  # sustain + density + keyCenter + 8 ranks * 4 values
        if len(combined_hex) != expected_length:
            raise ValueError(f"Expected {expected_length} hex chars, got {len(combined_hex)}")
        
        # Parse main parameters
        self.sustain = int(combined_hex[0], 16)
        self.density_function = int(combined_hex[1], 16)
        self.key_center = int(combined_hex[2], 16)
        
        # Parse ranks
        for i in range(8):
            start_idx = 3 + (i * 4)
            end_idx = start_idx + 4
            rank_hex = combined_hex[start_idx:end_idx]
            self.ranks[i] = [int(char, 16) for char in rank_hex]
    
    def get_changes(self, previous_state: 'SystemState') -> Dict[str, Any]:
        """Return only changed values for efficient processing"""
        changes = {}
        
        if self.sustain != previous_state.sustain:
            changes['sustain'] = self.sustain
        if self.density_function != previous_state.density_function:
            changes['density_function'] = self.density_function
        if self.key_center != previous_state.key_center:
            changes['key_center'] = self.key_center
        
        for i, (current_rank, prev_rank) in enumerate(zip(self.ranks, previous_state.ranks)):
            if current_rank != prev_rank:
                changes[f'rank_{i+1}'] = current_rank
        
        return changes
    
    def copy(self) -> 'SystemState':
        """Create a deep copy of the current state"""
        return SystemState(
            sustain=self.sustain,
            density_function=self.density_function,
            key_center=self.key_center,
            ranks=[rank.copy() for rank in self.ranks]
        )


class UDPHandler:
    """Handles UDP communication with MaxMSP"""
    
    def __init__(self, config: Config, message_processor: Callable[[str], None]):
        self.config = config
        self.message_processor = message_processor
        self.pending_inputs: Dict[tuple, InputState] = {}
        self.socket: Optional[socket.socket] = None
        self.send_socket: Optional[socket.socket] = None
    
    async def start_listener(self) -> None:
        """Start the UDP listener"""
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((self.config.host, self.config.listen_port))
        self.socket.setblocking(False)
        
        logger.info(f"UDP Listener started on {self.config.host}:{self.config.listen_port}")
        
        while True:
            try:
                data, addr = await asyncio.get_event_loop().sock_recvfrom(self.socket, 1024)
                if data:
                    await self.process_received_message(data, addr)
            except Exception as e:
                logger.error(f"Error in UDP listener: {e}")
                await asyncio.sleep(0.001)  # Prevent tight loop on error
    
    async def process_received_message(self, data: bytes, addr: tuple) -> None:
        """Process incoming UDP message and handle dual inputs"""
        try:
            address, input_data = self._decode_osc_message(data)
            logger.debug(f"Received message: address='{address}', data='{input_data}', length={len(input_data)}")
            
            if address in self.config.expected_input_flags:
                await self._handle_user_input(address, input_data, addr)
            elif address == "/uin":
                # Handle legacy single input format
                logger.info(f"Received legacy /uin format: '{input_data}'")
                await self.message_processor(input_data)
            else:
                logger.debug(f"Ignoring message with address: {address}")
        except Exception as e:
            logger.error(f"Error processing message from {addr}: {e}")
    
    async def _handle_user_input(self, flag: str, data: str, addr: tuple) -> None:
        """Handle uin1/uin2 inputs and combine when both received"""
        session_key = addr
        
        if session_key not in self.pending_inputs:
            self.pending_inputs[session_key] = InputState()
        
        input_state = self.pending_inputs[session_key]
        
        if input_state.add_input(flag, data):
            # Both inputs received - process combined input
            combined_hex = input_state.get_combined_hex()
            logger.debug(f"Combined hex input: {combined_hex}")
            await self.message_processor(combined_hex)
            
            # Clean up
            del self.pending_inputs[session_key]
        else:
            # Start timeout timer for incomplete input
            asyncio.create_task(self._timeout_incomplete_input(session_key))
    
    async def _timeout_incomplete_input(self, session_key: tuple) -> None:
        """Clean up incomplete inputs after timeout"""
        await asyncio.sleep(self.config.input_timeout_ms / 1000.0)
        
        if session_key in self.pending_inputs:
            input_state = self.pending_inputs[session_key]
            if not input_state.is_complete:
                logger.warning(f"Timeout: Incomplete input from {session_key}")
                del self.pending_inputs[session_key]
    
    def _decode_osc_message(self, data: bytes) -> Tuple[str, str]:
        """Decode OSC-like message from MaxMSP"""
        try:
            # Find the end of the address string
            address_end = data.find(b'\x00')
            if address_end == -1:
                raise ValueError("No null terminator found for address")
            
            # Decode address with error handling
            address = data[:address_end].decode('utf-8', errors='replace')
            
            # Adjust for OSC alignment (4-byte boundaries)
            data_offset = (address_end + 4) & ~0x03
            
            # Check if we have enough data
            if data_offset >= len(data):
                raise ValueError("Insufficient data for type tags")
            
            # Find type tag section
            data_tag_end = data.find(b'\x00', data_offset)
            if data_tag_end == -1:
                data_tag_end = len(data)
            
            # Calculate input data offset
            input_offset = (data_tag_end + 4) & ~0x03
            
            # Check if we have input data
            if input_offset >= len(data):
                return address, ""
            
            # Try to decode input data, with fallback for binary data
            input_data_bytes = data[input_offset:]
            
            # First, try to decode as UTF-8
            try:
                input_data = input_data_bytes.decode('utf-8').strip('\x00')
            except UnicodeDecodeError:
                # If that fails, try latin-1 (which can handle any byte value)
                try:
                    input_data = input_data_bytes.decode('latin-1').strip('\x00')
                except UnicodeDecodeError:
                    # As a last resort, convert bytes to hex string
                    input_data = input_data_bytes.hex()
                    logger.warning(f"Binary data received, converted to hex: {input_data}")
            
            return address, input_data
            
        except Exception as e:
            logger.error(f"Error decoding OSC message: {e}")
            logger.debug(f"Raw data: {data.hex()}")
            # Return empty values to prevent crash
            return "", ""
    
    async def send_message(self, address: str, data: int) -> None:
        """Send message back to MaxMSP"""
        try:
            if not self.send_socket:
                self.send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.send_socket.setblocking(False)
            
            message = self._encode_osc_message(address, data)
            await asyncio.get_event_loop().sock_sendto(
                self.send_socket, message, (self.config.host, self.config.send_port)
            )
            logger.debug(f"Sent message: {address} -> {data}")
        except Exception as e:
            logger.error(f"Error sending message: {e}")
    
    def _encode_osc_message(self, address: str, data: int) -> bytes:
        """Encode data into OSC-like structure"""
        # Encode address
        encoded_addr = address.encode('utf-8') + b'\x00'
        padding = (4 - len(encoded_addr) % 4) % 4
        encoded_addr += b'\x00' * padding
        
        # Encode data as integer
        encoded_data = b",i\x00\x00"
        encoded_data += data.to_bytes(4, byteorder='big', signed=True)
        
        return encoded_addr + encoded_data
    
    def close(self) -> None:
        """Close UDP sockets"""
        if self.socket:
            self.socket.close()
        if self.send_socket:
            self.send_socket.close()


class InputBuffer:
    """Manages input buffering with configurable timing"""
    
    def __init__(self, buffer_time_ms: int, processor: Callable[[str], None]):
        self.buffer_time_ms = buffer_time_ms
        self.processor = processor
        self.buffer = []
        self.buffer_lock = asyncio.Lock()
        self.buffer_event = asyncio.Event()
    
    async def add_combined_input(self, combined_hex: str) -> None:
        """Add complete combined hex input to buffer"""
        async with self.buffer_lock:
            self.buffer.append({
                'data': combined_hex,
                'timestamp': time.time()
            })
            self.buffer_event.set()
    
    async def process_buffer(self) -> None:
        """Process buffered inputs - takes most recent within time window"""
        while True:
            try:
                await self.buffer_event.wait()
                await asyncio.sleep(self.buffer_time_ms / 1000.0)
                
                async with self.buffer_lock:
                    if self.buffer:
                        # Get most recent input from buffer
                        latest_input = self.buffer[-1]['data']
                        self.buffer.clear()
                        self.buffer_event.clear()
                        
                        # Process the combined input
                        await self.processor(latest_input)
            except Exception as e:
                logger.error(f"Error in buffer processor: {e}")
                await asyncio.sleep(0.001)


class DBNEngine:
    """Dynamic Bayesian Network processing engine"""
    
    def __init__(self, udp_handler: UDPHandler):
        self.udp_handler = udp_handler
        self.current_state = SystemState()
        self.previous_state = SystemState()
    
    async def process_input(self, combined_hex: str) -> None:
        """Main processing logic for combined hex input"""
        try:
            # Update previous state
            self.previous_state = self.current_state.copy()
            
            # Parse new input
            self.current_state.update_from_combined_input(combined_hex)
            
            # Print all stored data
            self._print_system_state(combined_hex)
            
            # Process through DBN
            messages = await self._run_dbn_algorithm()
            
            # Send response messages
            for address, data in messages:
                await self.udp_handler.send_message(address, data)
                
        except Exception as e:
            logger.error(f"Error processing input: {e}")
            logger.error(f"Input that caused error: '{combined_hex}'")
            logger.error(f"Input length: {len(combined_hex)} characters")
            logger.error(f"Input as bytes: {combined_hex.encode('utf-8')}")
            # Print character-by-character breakdown for debugging
            logger.error("Character breakdown:")
            for i, char in enumerate(combined_hex):
                logger.error(f"  Position {i}: '{char}' (0x{ord(char):02X})")
    
    def _print_system_state(self, combined_hex: str) -> None:
        """Print all stored system data for debugging"""
        print("\n" + "="*60)
        print("NEW SIGNAL RECEIVED FROM MaxMSP")
        print("="*60)
        print(f"Raw Combined Hex Input: {combined_hex}")
        print(f"Input Length: {len(combined_hex)} characters")
        print("-"*60)
        print("PARSED SYSTEM STATE:")
        print(f"  Sustain:         {self.current_state.sustain} (0x{self.current_state.sustain:X})")
        print(f"  Density Function: {self.current_state.density_function} (0x{self.current_state.density_function:X})")
        print(f"  Key Center:      {self.current_state.key_center} (0x{self.current_state.key_center:X})")
        print("-"*60)
        print("RANK DATA:")
        for i, rank in enumerate(self.current_state.ranks, 1):
            hex_values = [f"0x{val:X}" for val in rank]
            print(f"  Rank {i}: {rank} -> {hex_values}")
        
        # Show changes from previous state
        changes = self.current_state.get_changes(self.previous_state)
        if changes:
            print("-"*60)
            print("CHANGES FROM PREVIOUS STATE:")
            for key, value in changes.items():
                if key.startswith('rank_'):
                    rank_num = key.split('_')[1]
                    hex_values = [f"0x{val:X}" for val in value]
                    print(f"  {key}: {value} -> {hex_values}")
                else:
                    print(f"  {key}: {value} (0x{value:X})")
        else:
            print("-"*60)
            print("NO CHANGES FROM PREVIOUS STATE")
        
        print("="*60)
        print()
    
    async def _run_dbn_algorithm(self) -> List[Tuple[str, int]]:
        """Execute DBN algorithm and return messages to send"""
        bypass_rank = self._check_bypass_rank()
        
        if bypass_rank == 0:
            # No changes - just update and send current values
            return await self._send_current_values()
        elif bypass_rank < 7:
            # Run full DBN algorithm
            return await self._execute_full_dbn()
        else:
            # Substitute specific rank
            return await self._substitute_rank(bypass_rank)
    
    def _check_bypass_rank(self) -> int:
        """Check which rank needs processing (0 = no changes)"""
        for i, (current_rank, prev_rank) in enumerate(zip(self.current_state.ranks, self.previous_state.ranks)):
            if current_rank != prev_rank:
                return i + 1
        return 0
    
    async def _send_current_values(self) -> List[Tuple[str, int]]:
        """Send current values to MaxMSP"""
        messages = [
            ("/sustain", self.current_state.sustain),
            ("/keyCenter", self.current_state.key_center)
        ]
        
        # Add voicemap if needed
        # messages.extend(await self._generate_voicemap())
        
        return messages
    
    async def _execute_full_dbn(self) -> List[Tuple[str, int]]:
        """Execute full DBN algorithm"""
        # Placeholder for actual DBN implementation
        logger.info("Executing full DBN algorithm")
        return await self._send_current_values()
    
    async def _substitute_rank(self, rank_index: int) -> List[Tuple[str, int]]:
        """Execute rank substitution algorithm"""
        # Placeholder for rank substitution implementation
        logger.info(f"Substituting rank {rank_index}")
        return await self._send_current_values()


class NecromoireController:
    """Main controller for the Necromoire system"""
    
    def __init__(self, config: Config = None):
        self.config = config or Config()
        self.udp_handler = UDPHandler(self.config, self._process_combined_input)
        self.dbn_engine = DBNEngine(self.udp_handler)
        self.input_buffer = InputBuffer(self.config.buffer_time_ms, self.dbn_engine.process_input)
        self.running = False
    
    async def _process_combined_input(self, combined_hex: str) -> None:
        """Process combined hex input through buffer"""
        await self.input_buffer.add_combined_input(combined_hex)
    
    async def run(self) -> None:
        """Start the main system"""
        self.running = True
        logger.info("Starting Necromoire Controller")
        
        try:
            # Start all components
            await asyncio.gather(
                self.udp_handler.start_listener(),
                self.input_buffer.process_buffer(),
                self._monitor_system()
            )
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
        finally:
            await self.shutdown()
    
    async def _monitor_system(self) -> None:
        """Monitor system health and log status"""
        while self.running:
            await asyncio.sleep(30)  # Log status every 30 seconds
            logger.info("System running normally")
    
    async def shutdown(self) -> None:
        """Graceful shutdown"""
        logger.info("Shutting down Necromoire Controller")
        self.running = False
        self.udp_handler.close()


async def main():
    """Main entry point"""
    config = Config()
    controller = NecromoireController(config)
    
    try:
        await controller.run()
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        await controller.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
