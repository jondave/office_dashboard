import socket
import serial
import time
import sys
import signal

# Serial connection settings
SERIAL_PORT = '/dev/ttyUSB1'  # Update with your serial port
BAUD_RATE = 115200

# Socket settings
HOST = '127.0.0.1'  # Localhost
PORT = 5001  # Port to communicate with the Flask server
# PORT = 65432  # Port to communicate with the Flask server

# Graceful shutdown flag
terminate_flag = False

def signal_handler(sig, frame):
    """Handle termination signals for graceful shutdown."""
    global terminate_flag
    print("Shutdown signal received. Exiting...")
    terminate_flag = True

# Register signal handler for graceful termination
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Function to read serial data and send it to the server
def send_serial_data():
    try:
        # Open serial connection
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        print(f"Connected to serial port {SERIAL_PORT} at {BAUD_RATE} baud.")
    except serial.SerialException as e:
        print(f"Failed to connect to serial port: {e}")
        sys.exit(1)

    retry_delay = 5  # Initial retry delay

    while not terminate_flag:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                # Try to connect to the server
                s.connect((HOST, PORT))
                print(f"Connected to {HOST}:{PORT}")
                retry_delay = 5  # Reset retry delay after successful connection
                
                while not terminate_flag:
                    if ser.in_waiting > 0:
                        # Read the line from the serial port, decode it, and strip any extraneous characters
                        line = ser.readline().decode('utf-8').strip()

                        # Example format: Temperature: 25.34 °C, Humidity: 60.23 %
                        if "Temperature" in line and "Humidity" in line:
                            try:
                                # Extract the temperature and humidity values from the string
                                temp_value = line.split(" ")[1]
                                humidity_value = line.split(" ")[4].strip('%')

                                # Send the data to the Flask server via socket
                                message = f"{temp_value},{humidity_value}"
                                s.sendall(message.encode('utf-8'))
                                print(f"Sent data: {message}")
                            except (IndexError, ValueError) as parse_error:
                                print(f"Parsing error: {parse_error} - Received line: {line}")
                    
                    time.sleep(60)  # Adjust the delay based on how frequently you want to send data

        except (ConnectionRefusedError, BrokenPipeError) as e:
            print(f"Connection error: {e}. Retrying in {retry_delay} seconds...")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)  # Exponential backoff, capped at 30 seconds
        except serial.SerialException as e:
            print(f"Serial error: {e}. Reopening serial connection...")
            ser.close()
            try:
                ser.open()
            except serial.SerialException as reopen_error:
                print(f"Failed to reopen serial port: {reopen_error}")
                time.sleep(5)
        except Exception as e:
            print(f"Unexpected error: {e}")
            time.sleep(5)

    # Close the serial port on exit
    ser.close()
    print("Serial port closed. Exiting...")

if __name__ == "__main__":
    send_serial_data()
