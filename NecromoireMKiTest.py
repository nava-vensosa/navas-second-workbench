import socket
import threading
import queue
import time




'''
The first section of this code is  UDP Network for receiving messages from
MaxMSP and sending messages back. The second section will be the DBN, which
processes the user inputs and determines what control signals to send back.
The main function runs the listening/sending threads.
'''


'''
Code for the UDP Network
'''

# Configuration for UDP communication with MaxMSP
localhost = "127.0.0.1"
listen_port = 1761  # Port for listening
send_port = 1762    # Port for sending

# Create a shared queue for communication between listening and sending threads
message_queue = queue.Queue()

# Configuration for the input buffer
"""N.b. you can change the buffer_time_ms to be shorter or longer to change how
responsive or quick the instrument is at reading user inputs"""
buffer_time_ms = 18  # Buffer time in milliseconds
input_buffer = []  # Shared buffer for user inputs
buffer_lock = threading.Lock()

# Modified UDP Listener function
def udp_listener():
    # Bind a socket to the listening port
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.bind((localhost, listen_port))
    print(f"Listening on {localhost}:{listen_port}")

    while True:
        try:
            # Receive uin data encoded in bytes from MaxMSP, and port address
            data, addr = udp_socket.recvfrom(1024)

            # Decode the message sent from MaxMSP
            if len(data) > 0:
                address_end = data.find(b'\x00')
                address = data[:address_end].decode('utf-8')

                # Receive user input data from MaxMSP
                if address == "/uin":
                    # Adjust for MaxMSP send data telomeres
                    data_offset = (address_end + 4) & ~0x03
                    data_tag_end = data.find(b'\x00', data_offset)
                    uin_offset = (data_tag_end + 4) & ~0x03

                    # Assign user inputs to variable named uin
                    uin = data[uin_offset:].decode('utf-8').strip('\x00')

                    # Add input to the buffer
                    with buffer_lock:
                        input_buffer.append(uin)

        except Exception as e:
            print(f"Error in listener: {e}")

# New function to process the buffer
def buffer_processor():
    while True:
        time.sleep(buffer_time_ms / 1000.0)  # Wait for the buffer duration

        with buffer_lock:
            if input_buffer:
                # Get the last input from the buffer
                last_input = input_buffer[-1]
                input_buffer.clear()  # Clear the buffer

                # Process the last input
                print(f"Processing buffered input: {last_input}")

                # Process user input and send control signals to MaxMSP
                messages = route_to_DBN(last_input)
                for message in messages:
                    message_queue.put((localhost, message[0], message[1]))

# UDP Sender function
def udp_sender():
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    while True:
        try:
            # Wait for processed data in the queue
            addr, send_address, data = message_queue.get()  # Blocking call
            message_queue.task_done()

            # Encode the message
            send_message = encode_data_in_bytes(send_address, data)

            # Send the message to the target address
            udp_socket.sendto(send_message, (localhost, send_port))
            print(f"Sent message to {localhost}:{send_port} -> Send Message: {send_message}")

        except Exception as e:
            print(f"Error in sender: {e}")

# Function to encode data into an OSC-like structure
def encode_data_in_bytes(addr, data):
    # Encode the address in bytes
    encoded_addr = addr.encode('utf-8') + b'\x00'
    padding_for_osc = (4 - len(encoded_addr) % 4) % 4
    encoded_addr += b'\x00' * padding_for_osc

    # Encode the data as an OSC integer
    encoded_data = b",i\x00\x00"  # Type tag for a single integer
    encoded_data += data.to_bytes(4, byteorder='big', signed=True)

    # Combine and return the full message
    return encoded_addr + encoded_data




'''
Code for the DBN
'''
# lists of ints that store variables' current and next values
sustain = [0, 0]
densityFunction = [0, 0]
keyCenter = [0, 0]

# lists of lists that store the ranks' current and next values
rank1 = [[0,0,0,0], [0,0,0,0]]
rank2 = [[0,0,0,0], [0,0,0,0]]
rank3 = [[0,0,0,0], [0,0,0,0]]
rank4 = [[0,0,0,0], [0,0,0,0]]
rank5 = [[0,0,0,0], [0,0,0,0]]
rank6 = [[0,0,0,0], [0,0,0,0]]
rank7 = [[0,0,0,0], [0,0,0,0]]
rank8 = [[0,0,0,0], [0,0,0,0]]

ranks = [rank1, rank2, rank3, rank4, rank5, rank6, rank7, rank8]


def greycode_to_int_helper(graycode):
    return int(hex(int(''.join(map(str, graycode)), 2))[2:].upper(), 16)
    # untested


# Function to parse user input and adjust all variables' next parameters for the DBN to run
# Returns the DBN(), which eventually returns a list of messages to send to MaxMSP
def route_to_DBN(uin):
    sustain[1] = int(uin[0], 16)
    densityFunction[1] = int(uin[1], 16)
    keyCenter[1] = int(uin[2], 16)
    for i, rank in enumerate(ranks, start=1):
        start = 3 + (i - 1) * 4
        end = start + 4
        rank[1] = list(map(lambda char: int(char, 16), uin[start:end]))
    return DBN()


#N.b. make the following branchless!!!!!

def checkBypass():
    for i, rank in enumerate(ranks, start=1):  # Use enumerate to track the index
        if rank[0] != rank[1]:  # Check if the condition is met
            return i  # Return the current index
    return 0  # Return 0 if no mismatch is found


def DBN():
    null_means_bypass = checkBypass()

    while null_means_bypass < 7:
        return actuallyRunDBN()
    while null_means_bypass:
        return subsRank()
    return updateNextsToCurrents()

def updateNextsToCurrents():
    print("TEST")
    print(f"sustain: {sustain}")
    print(f"densityFunction: {densityFunction}")
    print(f"keyCenter: {keyCenter}")
    print(f"rank1: {rank1}")
    print(f"rank2: {rank2}")
    print(f"rank3: {rank3}")
    print(f"rank4: {rank4}")
    print(f"rank5: {rank5}")
    print(f"rank6: {rank6}")
    print(f"rank7: {rank7}")
    print(f"rank8: {rank8}")
    print(f"\n\nranks: {ranks}")
    print("\n\n\n")

    sustain[0] = sustain[1]
    densityFunction[0] = densityFunction[1]
    keyCenter[0]= keyCenter[1]

    for rank in ranks:
        rank[0] = rank[1]

    print("TEST")
    print(f"sustain: {sustain}")
    print(f"densityFunction: {densityFunction}")
    print(f"keyCenter: {keyCenter}")
    print(f"rank1: {rank1}")
    print(f"rank2: {rank2}")
    print(f"rank3: {rank3}")
    print(f"rank4: {rank4}")
    print(f"rank5: {rank5}")
    print(f"rank6: {rank6}")
    print(f"rank7: {rank7}")
    print(f"rank8: {rank8}")
    print(f"\n\nranks: {ranks}")
    print("\n\n\n")

    return sendValues()

def sendValues():
    return [["/sustain", sustain[0]], ["/keyCenter", keyCenter[0]]] # + sendVoicemap()

def sendVoicemap():
    return ["/fuck", 0]


def actuallyRunDBN():
    return updateNextsToCurrents()

def subsRank():
    return updateNextsToCurrents()





















'''
Code for running the listening/sending threads
'''


# Main function to start the threads
def main():
    # Create and start the listener thread
    listener_thread = threading.Thread(target=udp_listener, daemon=True)
    listener_thread.start()

    # Create and start the buffer processor thread
    buffer_thread = threading.Thread(target=buffer_processor, daemon=True)
    buffer_thread.start()

    # Create and start the sender thread
    sender_thread = threading.Thread(target=udp_sender, daemon=True)
    sender_thread.start()

    # Keep the main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down.")

# Run the main function
if __name__ == "__main__":
    main()
