# TODO: Create seperate log files for each day to reduce load time
# TODO: Auto swap the log time on day change
# TODO: Display only the current day's data in the graphs

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
import threading
import time
import datetime
import random
import csv
import os
import socket
import requests
import json
import signal
from xml.etree import ElementTree as ET
from lxml import etree
import pyttsx3

app = Flask(__name__)
socketio = SocketIO(app)

# Define a shutdown flag to signal the thread to stop
shutdown_flag = threading.Event()

# Lists to store temperature, humidity, and timestamps for plotting
temperature_data = []
humidity_data = []
timestamps = []

# CSV file to store the sensor data
CSV_FILE = 'sensor_data.csv'

# Socket settings (to communicate with serial_reader.py)
HOST = '127.0.0.1'
PORT = 5001
# PORT = 65432

# API endpoints and keys
BBC_RSS_FEED = "http://feeds.bbci.co.uk/news/rss.xml"
ONION_RSS_FEED = "https://www.theonion.com/rss"
DAILY_MASH_RSS_FEED = "https://www.thedailymash.co.uk/feed"
WEATHER_URL = "http://api.openweathermap.org/data/2.5/weather"
AIRPORT_CODE = "HUY"
FLIGHT_API_URL = "http://api.aviationstack.com/v1/flights"
TRAIN_API_URL = "https://lite.realtime.nationalrail.co.uk/OpenLDBWS/ldb11.asmx"

# Load the configuration data
with open('config/config.json', 'r') as file:
    config = json.load(file)

# Access API keys
TRAIN_API_KEY = config.get("TRAIN_API_KEY")
FLIGHT_API_KEY = config.get("FLIGHT_API_KEY")
OPENWEATHER_API_KEY = config.get("OPENWEATHER_API_KEY")
OPEN_BUS_DATA_API_KEY = config.get("OPEN_BUS_DATA_API_KEY")

# Function to load historical data from the CSV file when the server starts
def load_data_from_csv():
    if os.path.exists(CSV_FILE):
        with open(CSV_FILE, 'r') as csvfile:
            reader = csv.reader(csvfile)
            for row in reader:
                timestamp_str, temperature, humidity = row
                timestamp = datetime.datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                timestamps.append(timestamp)
                temperature_data.append(float(temperature))
                humidity_data.append(float(humidity))

    # Keep only the data points from the last 5 minutes for plotting
    filter_old_data()

# Function to log data to file and send it via WebSocket
def log_and_send_data(temperature, humidity):
    timestamp = datetime.datetime.now()

    # Append data to the lists
    timestamps.append(timestamp)
    temperature_data.append(temperature)
    humidity_data.append(humidity)

    # Save the new data to the CSV file
    with open(CSV_FILE, 'a', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([timestamp.strftime("%Y-%m-%d %H:%M:%S"), temperature, humidity])

    # Remove data points older than 5 minutes for real-time display
    filter_old_data()

    # Calculate the 5-minute average temperature
    average_temperature_5min = sum(temperature_data) / len(temperature_data) if len(temperature_data) > 0 else 0

    # Calculate the 5-day average temperature
    average_temperature_5days = calculate_5_day_average()

    # Emit the current and average temperatures along with the latest data points
    socketio.emit('new_data', {
        'time': timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        'temperature': temperature,
        'humidity': humidity,
        'current_temperature': temperature,
        'average_temperature_5min': average_temperature_5min,
        'average_temperature_5days': average_temperature_5days
    })

# Function to filter out data older than 5 minutes for chart plotting
def filter_old_data():
    current_time = datetime.datetime.now()
    five_minutes_ago = current_time - datetime.timedelta(minutes=5)

    # Keep only the data points within the last 5 minutes
    while len(timestamps) > 0 and timestamps[0] < five_minutes_ago:
        timestamps.pop(0)
        temperature_data.pop(0)
        humidity_data.pop(0)

# Function to calculate the average temperature over the last 5 days
def calculate_5_day_average():
    current_time = datetime.datetime.now()
    five_days_ago = current_time - datetime.timedelta(days=5)

    # Filter data within the last 5 days
    temperatures_last_5days = [temp for i, temp in enumerate(temperature_data) if timestamps[i] >= five_days_ago]

    # Calculate the average temperature over the last 5 days
    if len(temperatures_last_5days) > 0:
        return sum(temperatures_last_5days) / len(temperatures_last_5days)
    else:
        return 0  # Return 0 if no data is available for the last 5 days

# Function to receive data from the socket connection
def receive_socket_data():
    """Function to receive data from the internal socket connection."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # Allow socket reuse
    s.bind((HOST, PORT))
    s.listen()

    print(f"Listening on {HOST}:{PORT} for serial data...")

    while not shutdown_flag.is_set():
        try:
            # Wait for a client connection
            # print("Waiting for client to connect...")
            s.settimeout(1.0)  # Timeout to check shutdown flag
            try:
                conn, addr = s.accept()
                print(f"Connected by {addr}")
                with conn:
                    while not shutdown_flag.is_set():
                        try:
                            conn.settimeout(1.0)  # Set timeout to avoid blocking
                            data = conn.recv(1024)
                            if not data:
                                print("Connection lost. Waiting for reconnection...")
                                break  # Client disconnected, retry to accept a new connection
                            
                            # Parse received data (temperature, humidity)
                            temp_value, humidity_value = data.decode('utf-8').strip().split(',')
                            log_and_send_data(float(temp_value), float(humidity_value))

                        except socket.timeout:
                            # Timeout allows the loop to check shutdown_flag regularly
                            continue

                        except Exception as e:
                            print(f"Error receiving socket data: {e}")
                            break
            except socket.timeout:
                # No client connected yet, retry after timeout
                continue

        except Exception as e:
            print(f"Error with socket server: {e}")
            time.sleep(1)  # Wait a moment before retrying in case of an error
    
    s.close()
    print("Socket server closed.")

# Route to serve the main webpage
@app.route('/')
def home():
    return render_template('index.html')

# Route to fetch latest news
@app.route('/news')
def news():
    try:
        news = []

        # Function to fetch and parse RSS feed
        def fetch_rss(url):
            response = requests.get(url)
            response.raise_for_status()  # Raise exception for bad response
            root = ET.fromstring(response.content)
            return root.findall(".//item")[:6]  # Get latest 6 articles

        # Fetch articles from both RSS feeds
        bbc_items = fetch_rss(BBC_RSS_FEED)
        onion_items = fetch_rss(ONION_RSS_FEED)
        mash_items = fetch_rss(DAILY_MASH_RSS_FEED)

        # Extract and combine articles from all sources
        for item in bbc_items + onion_items + mash_items:
            title = item.find("title").text
            link = item.find("link").text
            news.append({"title": title, "link": link})

        # Shuffle the combined list to mix news randomly
        random.shuffle(news)

        return jsonify(news)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Route to fetch weather information
@app.route('/weather/<city>')
def get_weather(city):
    params = {
        'q': city,
        'appid': OPENWEATHER_API_KEY,
        'units': 'metric'  # Get temperature in Celsius
    }
    try:
        response = requests.get(WEATHER_URL, params=params)
        response.raise_for_status()
        data = response.json()

        # Extract relevant weather information safely
        weather = {
            'city': data.get('name', 'Unknown'),
            'temperature': data['main'].get('temp', 'N/A'),
            'description': data['weather'][0].get('description', 'N/A').title(),
            'id': data['weather'][0].get('id', 0),
            'icon': data['weather'][0].get('icon', '')
        }
        return jsonify(weather)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# # Route to fetch flight departure information from Humberside Airport
# MAX_FLIGHTS = 5  # Change this to the desired number
# @app.route('/departures_airport')
# def departures_airport():
#     try:
#         # Fetch flight data
#         response = requests.get(FLIGHT_API_URL, params={"access_key": FLIGHT_API_KEY, "dep_iata": AIRPORT_CODE})
#         response.raise_for_status()
        
#         data = response.json()
#         departures = []

#         # Extract departure information with a limit
#         count = 0  # Initialize a counter
#         for flight in data.get("data", []):
#             departures.append({
#                 "flight_number": flight["flight"]["iata"],
#                 "destination": flight["departure"]["iata"],
#                 "departure_time": flight["departure"]["estimated"],
#             })
#             count += 1  # Increment the counter
#             if count >= MAX_FLIGHTS:  # Check if the limit is reached
#                 break

#         return jsonify(departures)
#     except Exception as e:
#         return jsonify({"error": str(e)}), 500

@app.route('/departures/<departure_station>', methods=['GET'])
def departures(departure_station):
    # departure_station = "LCN"  # Set your departure station here

    # Ensure the departure_station is in uppercase
    departure_station = departure_station.upper()

    if not departure_station:
        return "Please provide a departureStation parameter", 400

    APIRequest = f"""
        <x:Envelope xmlns:x="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ldb="http://thalesgroup.com/RTTI/2017-10-01/ldb/" xmlns:typ4="http://thalesgroup.com/RTTI/2013-11-28/Token/types">
            <x:Header>
                <typ4:AccessToken>
                    <typ4:TokenValue>{TRAIN_API_KEY}</typ4:TokenValue>
                </typ4:AccessToken>
            </x:Header>
            <x:Body>
                <ldb:GetDepBoardWithDetailsRequest>
                    <ldb:numRows>10</ldb:numRows>
                    <ldb:crs>{departure_station}</ldb:crs>
                    <ldb:filterCrs></ldb:filterCrs>
                    <ldb:filterType>to</ldb:filterType>
                    <ldb:timeOffset>0</ldb:timeOffset>
                    <ldb:timeWindow>120</ldb:timeWindow>
                </ldb:GetDepBoardWithDetailsRequest>
            </x:Body>
        </x:Envelope>
    """

    headers = {'Content-Type': 'text/xml'}

    # try:
    response = requests.post(TRAIN_API_URL, data=APIRequest, headers=headers)
    response.raise_for_status()

    # Parse the XML response
    departure_station_name, departure_station_code, departure_data = parse_departures(response.text)
    if departure_data == None:
        raise Exception("parsing returned None. Rob's Fault")
    
     # Render the departures template with the parsed data
    # threading.Thread(target=speak_first_train, args=(departure_data,), daemon=True).start()
    speak_first_train(departure_data)
    
    # return response.text
    
    # Render the departures template with the parsed data
    return render_template('departures.html', 
                           departure_data = departure_data, 
                           departure_station_name = departure_station_name)

@app.route('/train_departures/<departure_station>', methods=['GET'])
def train_departures(departure_station):
    # Ensure the departure_station is in uppercase
    departure_station = departure_station.upper()

    # You can set a default value here if needed
    # departure_station = departure_station or "LCN"  # Uncomment if you want a default value

    # Prepare the XML request
    APIRequest = f"""
        <x:Envelope xmlns:x="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ldb="http://thalesgroup.com/RTTI/2017-10-01/ldb/" xmlns:typ4="http://thalesgroup.com/RTTI/2013-11-28/Token/types">
            <x:Header>
                <typ4:AccessToken>
                    <typ4:TokenValue>{TRAIN_API_KEY}</typ4:TokenValue>
                </typ4:AccessToken>
            </x:Header>
            <x:Body>
                <ldb:GetDepBoardWithDetailsRequest>
                    <ldb:numRows>10</ldb:numRows>
                    <ldb:crs>{departure_station}</ldb:crs>
                    <ldb:filterCrs></ldb:filterCrs>
                    <ldb:filterType>to</ldb:filterType>
                    <ldb:timeOffset>0</ldb:timeOffset>
                    <ldb:timeWindow>120</ldb:timeWindow>
                </ldb:GetDepBoardWithDetailsRequest>
            </x:Body>
        </x:Envelope>
    """

    headers = {'Content-Type': 'text/xml'}

    try:
        response = requests.post(TRAIN_API_URL, data=APIRequest, headers=headers)
        response.raise_for_status()

        # Parse the XML response
        departure_station_name, departure_station_code, departure_data = parse_departures(response.text)
        if departure_data is None:
            return jsonify({"error": "Parsing returned None."}), 500

        # Return a JSON response
        return jsonify({
            "station_name": departure_station_name,
            "station_code": departure_station_code,
            "departures": departure_data  # Ensure this is a list of departures
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def parse_departures(xml_data):

    # Parse the XML
    root = ET.fromstring(xml_data)

    # Use the namespaces defined in the XML to correctly access elements
    namespaces = {
        'soap': 'http://schemas.xmlsoap.org/soap/envelope/',
        'ldb': 'http://thalesgroup.com/RTTI/2017-10-01/ldb/',
        'lt4': 'http://thalesgroup.com/RTTI/2015-11-27/ldb/types',
        'lt5': 'http://thalesgroup.com/RTTI/2016-02-16/ldb/types',
        'lt7': 'http://thalesgroup.com/RTTI/2017-10-01/ldb/types'
    }
    
    departure_data = []

    # Extract station details
    departure_station_name = (name_element.text if (name_element := root.find('.//lt4:locationName', namespaces)) is not None else "Unknown")
    departure_station_code = (code_element.text if (code_element := root.find('.//lt4:crs', namespaces)) is not None else "Unknown")
    # print(f"Departure Station: {departure_station_name} ({departure_station_code})")

    # Extract train services
    services = root.findall('.//lt7:service', namespaces)
    for service in services:
        std = (std_element.text if (std_element := service.find('.//lt4:std', namespaces)) is not None else "Unknown")
        etd = (etd_element.text if (etd_element := service.find('.//lt4:etd', namespaces)) is not None else "Unknown")
        platform = (platform_element.text if (platform_element := service.find('.//lt4:platform', namespaces)) is not None else "No Platform Yet")
        operator = (operator_element.text if (operator_element := service.find('.//lt4:operator', namespaces)) is not None else "Unknown")
        destination_name = (std_element.text if (std_element := service.find('.//lt5:destination/lt4:location/lt4:locationName', namespaces)) is not None else "Unknown")
        calling_points = service.findall('.//lt7:callingPoint', namespaces)
        # Extract (locationName, st) for each calling point or provide fallback
        intermediate_destinations = [
            (cp.find('lt7:locationName', namespaces).text, 
            cp.find('lt7:st', namespaces).text)
            for cp in calling_points
            if cp.find('lt7:locationName', namespaces) is not None and
            cp.find('lt7:st', namespaces) is not None
        ] if calling_points else [("Unknown", "Unknown")]  

        # print(f"\nTrain at {std} (Expected: {etd}) on Platform {platform}")
        # print(f"Operator: {operator}")
        # print(f"Destination: {destination_name}")

        departure_data.append({'std': std, 'etd': etd, 'platform': platform, 'operator': operator, 'destination_name': destination_name, 'intermediate_destinations': intermediate_destinations})

    # Check if there are at least 4 trains in the departure_data
    if len(departure_data) >= 4:
        # Extract std times from the 2nd and 4th entries
        std_time_2 = datetime.datetime.strptime(departure_data[1]['std'], '%H:%M')
        std_time_4 = datetime.datetime.strptime(departure_data[3]['std'], '%H:%M')
        
        # Generate a random std time between the 2nd and 4th trains' std times
        random_std = random_time_between(std_time_2, std_time_4).strftime('%H:%M')
    else:
        # Handle cases with fewer than 4 trains, Get the current time and add one hour
        current_time = datetime.datetime.now()
        one_hour_later = current_time + datetime.timedelta(hours=1)
        random_std = one_hour_later.strftime('%H:%M')  # Format the time as HH:MMins

    departure_data.insert(min(3, len(departure_data)), {'std': random_std, 'etd': 'On time', 'platform': '9¾', 'operator': 'Hogwarts Express', 'destination_name': 'Hogsmeade'})

    return departure_station_name, departure_station_code, departure_data

engine = pyttsx3.init()

def speak_first_train(departure_data):
    # while True:
        # Wait for 5 minutes        
        # time.sleep(300)  # 300 seconds = 5 minutes

    if departure_data and len(departure_data) > 0:
        # Get the first train's details
        first_train = departure_data[0]
        std = first_train['std']
        destination_name = first_train['destination_name']
        platform = first_train['platform']
        operator = first_train['operator']
        
        # Construct the speech text
        speech_text = f"The next train to depart from platform {platform} will be the {std} {operator} service to {destination_name}."
        
        # Speak the text
        engine.say(speech_text)
        engine.runAndWait()

@app.route('/bus_map')
def bus_map():
    return render_template('bus_map.html')

@app.route('/bus_data')
def bus_data():
    # Define the bounding box coordinates [minLongitude, minLatitude, maxLongitude, maxLatitude]
    bounding_box = "-0.60,53.20,-0.47,53.28"

    url_pc_coaches = "https://data.bus-data.dft.gov.uk/api/v1/datafeed/7861/?api_key=" + OPEN_BUS_DATA_API_KEY + "&boundingBox=" + bounding_box
    url_stagecoach_east_midlands = "https://data.bus-data.dft.gov.uk/api/v1/datafeed/7035/?api_key=" + OPEN_BUS_DATA_API_KEY + "&boundingBox=" + bounding_box

    response_pc_coaches = requests.get(url_pc_coaches)
    response_stagecoach_east_midlands = requests.get(url_stagecoach_east_midlands)

    geojson_pc_coaches = vehicle_data_xml_to_geojson(response_pc_coaches.content)
    geojson_stagecoach_east_midlands = vehicle_data_xml_to_geojson(response_stagecoach_east_midlands.content)

    # Combine features from both GeoJSON responses
    combined_features = geojson_pc_coaches['features'] + geojson_stagecoach_east_midlands['features']
    combined_geojson = {
        "type": "FeatureCollection",
        "features": combined_features
    }

    return jsonify(combined_features)

def vehicle_data_xml_to_geojson(xml_data):
    """Convert the XML vehicle data to GeoJSON format."""
    root = ET.fromstring(xml_data)
    namespace = {"siri": "http://www.siri.org.uk/siri"}
    
    features = []
    for vehicle in root.findall(".//siri:VehicleActivity", namespace):
        location = vehicle.find(".//siri:VehicleLocation", namespace)
        longitude = float(location.find("siri:Longitude", namespace).text)
        latitude = float(location.find("siri:Latitude", namespace).text)

        # Extract recorded time and format it
        recorded_time = vehicle.find(".//siri:RecordedAtTime", namespace).text
        formatted_time = format_time(recorded_time)

        # Build GeoJSON feature
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [longitude, latitude]
            },
            "properties": {
                "vehicleRef": vehicle.find(".//siri:VehicleRef", namespace).text,
                "lineRef": vehicle.find(".//siri:LineRef", namespace).text,
                "originName": vehicle.find(".//siri:OriginName", namespace).text,
                "destinationName": vehicle.find(".//siri:DestinationName", namespace).text,
                "operatorRef": vehicle.find(".//siri:OperatorRef", namespace).text,
                "recordedAtTime": formatted_time
            }
        }

        features.append(feature)

    geojson = {"type": "FeatureCollection", "features": features}
    return geojson

def format_time(iso_time):
    """Convert ISO 8601 time to a more readable format."""
    dt = datetime.datetime.fromisoformat(iso_time)
    return dt.strftime('%d %b %Y, %I:%M %p')  # Example: 21 Oct 2024, 08:53 AM

# Generate a random time between two given times
def random_time_between(start, end):
    delta = end - start
    random_seconds = random.randint(0, int(delta.total_seconds()))
    return start + datetime.timedelta(seconds=random_seconds)
        
# WebSocket connection handler to send historical data to newly connected clients
@socketio.on('connect')
def handle_connect():
    # Send the historical data (last 5 minutes) to the newly connected client
    recent_timestamps = [t.strftime("%Y-%m-%d %H:%M:%S") for t in timestamps]
    emit('historical_data', {
        'timestamps': recent_timestamps,
        'temperature_data': temperature_data,
        'humidity_data': humidity_data
    })

def shutdown_gracefull(signum, frame):
    """Signal handler to stop the socket thread and clean up."""
    print("Shutdown signal received. Stopping socket thread and Flask server...")
    
    # Stop the socket thread
    shutdown_flag.set()
    if socket_thread.is_alive():
        socket_thread.join()
    print("Socket thread stopped.")
    
    # Forcefully stop the Flask-SocketIO server
    os._exit(0)  # Forcefully exit the Flask application


# Register signal handlers for SIGINT and SIGTERM
signal.signal(signal.SIGINT, shutdown_gracefull)
signal.signal(signal.SIGTERM, shutdown_gracefull)

if __name__ == '__main__':

    # Load historical data from the CSV file when the server starts
    # load_data_from_csv()

    print("Socket Thead Starting")
    # Start a background thread to receive data from the internal socket
    socket_thread = threading.Thread(target=receive_socket_data)
    socket_thread.daemon = True
    socket_thread.start()


    print("Flask App Starting")
    # Start the Flask-SocketIO server
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
