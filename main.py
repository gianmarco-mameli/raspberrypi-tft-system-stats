#!/usr/bin/env python3

import warnings
warnings.filterwarnings("ignore")

import os
import signal
import socket
import sys
import threading
import time
import paho.mqtt.client as mqtt
import psutil
import random
from influxdb_client import InfluxDBClient
from hurry.filesize import size
import luma.core.render
from luma.core.interface.serial import spi
from luma.lcd.device import st7789
from luma.core.sprite_system import framerate_regulator
from PIL import Image, ImageDraw, ImageFont

# environment variables
mqtt_server = os.getenv("MQTT_SERVER")
motion_topic = os.getenv("MOTION_TOPIC")
lcd_rotation = int(os.getenv("LCD_ROTATION", 1))
influxdb_url = os.getenv("INFLUXDB_URL")
influxdb_org = os.getenv("INFLUXDB_ORG")
influxdb_bucket = os.getenv("INFLUXDB_BUCKET")
influxdb_token = os.getenv("INFLUXDB_TOKEN")

# import RPi.GPIO as GPIO

# # Set up GPIO22 as output
# GPIO.setmode(GPIO.BCM)
# GPIO.setup(17, GPIO.OUT)
# GPIO.setup(22, GPIO.OUT)

serial = spi(spi_mode=3, port=0, device=0, gpio_LIGHT=27, gpio_DC=17, gpio_RST=22, bus_speed_hz=8000000)
lcd = st7789(serial, rotate=lcd_rotation)
width = lcd.width
height = lcd.height

default_color = "#FFFFFF"
alternate_color = "#AA9C23"
outline_color = "#000000"
fill_color = "#000000"
logo = Image.open("raspberrypi.png")
img = Image.new("RGB", (width, height), color=(0, 0, 0))
draw = ImageDraw.Draw(img)
imgss = Image.new("RGB", (width, height), color=(0, 0, 0))
drawss = ImageDraw.Draw(imgss)

# Boot splashscreen
font = ImageFont.load_default(21)
font_big = ImageFont.load_default(29)
boot_text_1 = "Raspberry Pi Stats"
boot_text_2 = "Starting..."
print(boot_text_1)
print(boot_text_2)
w = draw.textlength(text=boot_text_1, font=font_big)
draw.text((int((width-w)/2),2), text=boot_text_1, font=font_big, fill=default_color)
w = draw.textlength(text=boot_text_2, font=font_big)
draw.text((int((width-w)/2),30), text=boot_text_2, font=font_big, fill=default_color)
w = int(logo.width)
img.paste(logo, (int((width-w)/2),90))
lcd.display(img)

padding = -3
top = padding
bottom = height - padding
global x
x = 3
data = {}
show_stats = True

influxdbc = None
if influxdb_url and influxdb_token and influxdb_org and influxdb_bucket:
    influxdbc = InfluxDBClient(url=influxdb_url, token=influxdb_token, org=influxdb_org)
    query_api = influxdbc.query_api()
    try:
        health = influxdbc.health()
        print(f"InfluxDB2 connection status: {health.status} - {health.message}")
    except Exception as e:
        print(f"InfluxDB2 connection error: {e}")

class screensaver(object):
    def __init__(self, w, h, image):
        lcd.clear()
        self._w = w
        self._h = h
        self._image = image
        self._imgWidth = image.width
        self._imgHeight = image.height
        self._x_speed = 1 #(random.random() - 0.5) * 10
        self._y_speed = 1 #(random.random() - 0.5) * 10
        self._x_pos = random.random() * self._w / 2.0
        self._y_pos = random.random() * self._h / 2.0

    def update_pos(self):
        if self._x_pos + self._imgWidth > self._w:
            self._x_speed = -abs(self._x_speed)
        elif self._x_pos  < 0.0:
            self._x_speed = abs(self._x_speed)

        if self._y_pos + self._imgHeight > self._h:
            self._y_speed = -abs(self._y_speed)
        elif self._y_pos < 0.0:
            self._y_speed = abs(self._y_speed)

        self._x_pos += self._x_speed
        self._y_pos += self._y_speed

    def draw(self):
        imgss.paste(self._image, (int(self._x_pos),int(self._y_pos)))

class SignalHandler:
    shutdown_requested = False

    def __init__(self):
        signal.signal(signal.SIGINT, self.request_shutdown)
        signal.signal(signal.SIGTERM, self.request_shutdown)

    def request_shutdown(self, signum, frame):
        if not self.shutdown_requested:
            print('Request to shutdown received, stopping')
            self.shutdown_requested = True
            try:
                lcd.clear()
            except Exception as e:
                print(f"LCD clear error: {e}")
            try:
                lcd.cleanup()
            except Exception as e:
                print(f"LCD cleanup error: {e}")
            sys.exit(0)

    def can_run(self):
        return not self.shutdown_requested

def on_connect(mqttc, userdata, flags, rc):
    print(f"MQTT Connected with result code {rc}")
    mqttc.subscribe(motion_topic)

def on_message(mqttc, userdata, msg,):
    global show_stats
    # if "motion" in msg.topic:
    if str(msg.payload.decode("utf-8")) == "1":
        mqttc.publish(hostname + "/stats_display", "1")
        show_stats = True
    if str(msg.payload.decode("utf-8")) == "0":
        show_stats = False
        mqttc.publish(hostname + "/stats_display", "0")
    # print(f"Show stats: {show_stats}")

def set_color(value, warn, crit):
    if warn != None and crit != None:
        if value >= crit:
            return "#bd0940"
        elif value >= warn:
            return "#aa9c23"
        else:
            return "#75aa23"
    else:
        return default_color

def first(iterable, default=None):
    if iterable:
        for item in iterable:
            return item
    return default

def intersect(a, b):
    return list(set(a) & set(b))

def get_value(metric_name, metric_value, metric_path):
    if influxdbc:
        flux_query = f'''from(bucket: "{influxdb_bucket}")
            |> range(start: -1h)
            |> filter(fn: (r) => r["_measurement"] == "{metric_name}")
            |> filter(fn: (r) => r["_field"] == "{metric_value}")
            |> filter(fn: (r) => r["hostname"] =~ /{hostname}/)
            |> filter(fn: (r) => r["metric"] == "{metric_path}")
            |> yield(name: "last")'''
        try:
            tables = query_api.query(flux_query)
            for table in tables:
                for record in table.records:
                    return record.get_value()
        except Exception as e:
            print(f"InfluxDB query error: {e}")
            return None
    return None

def get_info(service, path):
    service_name = service.split(".")[0]
    info = {
        service_name + "_crit": get_value(service, "crit", path),
        service_name + "_warn": get_value(service, "warn", path),
        service_name + "_max": get_value(service, "max", path),
    }
    return info

def get_iface_speed(iface):
    try:
        import subprocess
        result = subprocess.run(["ethtool", iface], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if "Speed:" in line:
                speed_str = line.split("Speed:")[1].strip()
                if speed_str.endswith("Mb/s"):
                    return int(speed_str.replace("Mb/s", "").strip())
        return 0
    except Exception:
        return 0

def get_data():
    global hostname, ip, net_speed, cpu_freq_max, load_info, temp_info, disk_info, mem_info, procs_info

    # hostname = socket.gethostname()
    hostname = "rpitools"
 
    net_if_stats = psutil.net_if_stats()
    net_if_addrs = psutil.net_if_addrs()
    active_iface = None
    net_speed = 0
    ip = ""

    for iface in ["eth0", "wlan0"]:
        if iface in net_if_stats and net_if_stats[iface].isup:
            active_iface = iface
            net_speed = get_iface_speed(iface)
            # Get IPv4 address
            for addr in net_if_addrs[iface]:
                if addr.family == socket.AF_INET:
                    ip = addr.address
                    break
            break

    cpu_freq_max = cpu_clock = psutil.cpu_freq().max

    load_info = get_info("load", "load1")
    temp_info = get_info("check_rpi", "cputemp")
    disk_info = get_info("disk", "/")
    mem_info = get_info("mem", "USED")
    procs_info = get_info("procs", "procs")

    if influxdb_url != None:
        print("Using InfluxDB data")

    print("New data fetched")

    time.sleep(3600)

def update_data():
    while True:
        global old_net_value, new_net_value
        if "old_net_value" not in globals():

            old_net_value = 0
        if "new_net_value" not in globals():
            new_net_value = 0

        cpu_used = round(psutil.getloadavg()[1], 1)
        cpu_clock = psutil.cpu_freq().current
        cpu_temp = round(
            (list(psutil.sensors_temperatures().values())[0])[0].current, 1
        )
        mem = psutil.virtual_memory()
        mem_used = mem.total - mem.available
        df = psutil.disk_usage("/").used
        df_total = psutil.disk_usage("/").total

        # network_ifs = psutil.net_if_stats().keys()
        # wlan0 = first(intersect(network_ifs, ["wlan0", "wl0"]), "wlan0")
        # eth0 = first(intersect(network_ifs, ["eth0", "en0"]), "eth0")

        new_net_value = (
            psutil.net_io_counters().bytes_sent + psutil.net_io_counters().bytes_recv
        )
        net_data = new_net_value - old_net_value
        old_net_value = new_net_value

        procs = len([key for key in psutil.process_iter()])

        global data
        data = {
            "cpu_used": cpu_used,
            "cpu_clock": cpu_clock,
            "cpu_temp": cpu_temp,
            "mem_total": mem.total,
            "mem_used": mem_used,
            "df": df,
            "df_total": df_total,
            "net_data": net_data,
            "procs": procs,
        }

        time.sleep(2)

def main(num_iterations=sys.maxsize):

    # x = 0

    thread_get = threading.Thread(target=get_data, daemon=True)
    thread_get.start()

    thread_update = threading.Thread(target=update_data, daemon=True)
    thread_update.start()

    frame_count = 0

    regulator = framerate_regulator(fps=0)

    time.sleep(10)

    if mqtt_server != None:
        mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, socket.gethostname() + "_stats_display")
        mqttc.on_connect = on_connect
        mqttc.on_message = on_message
        mqttc.connect(mqtt_server)
        mqttc.loop_start()
        
    global t_start
    t_start = time.time()

    ss = [screensaver(width, height, i) for i in [logo]]

    while signal_handler.can_run():
        while show_stats and signal_handler.can_run():
            draw.rectangle((0, 0, width, height), outline=outline_color, fill=fill_color)

            y = top

            Hostname = str(hostname)
            state_color = default_color
            draw.text((x, y), Hostname.upper(), font=font_big, fill=alternate_color)
            y += font.getbbox(Hostname)[3]
            y += 6

            Ip = str(ip)
            state_color = default_color
            draw.text((x, y), Ip, font=font, fill=state_color)
            y += font.getbbox(Ip)[3]

            y += 11
            draw.line([(0, y), (width, y)], default_color, 2)
            y += 7

            Load = "Cpu Load: " + str(data["cpu_used"])
            state_color = set_color(
                data["cpu_used"], load_info["load_warn"], load_info["load_crit"]
            )
            draw.text((x, y), Load, font=font, fill=state_color)
            y += font.getbbox(Load)[3]

            Clock = "Cpu Clock: " + str(data["cpu_clock"])
            state_color = set_color(data["cpu_clock"], (cpu_freq_max * 0.80), cpu_freq_max)
            draw.text((x, y), Clock, font=font, fill=state_color)
            y += font.getbbox(Clock)[3]

            Temp = "Temp: " + str(data["cpu_temp"]) + "°C"
            state_color = set_color(
                data["cpu_temp"],
                temp_info["check_rpi_warn"],
                temp_info["check_rpi_crit"],
            )
            draw.text((x, y), Temp, font=font, fill=state_color)
            y += font.getbbox(Temp)[3]

            Mem = "Mem: " + str(size(data["mem_used"])) + "/" + str(size(data["mem_total"]))
            state_color = set_color(
                data["mem_used"], mem_info["mem_warn"], mem_info["mem_crit"]
            )
            draw.text((x, y), Mem, font=font, fill=state_color)
            y += font.getbbox(Mem)[3]

            Disk = "Disk: " + str(size(data["df"])) + "/" + str(size(data["df_total"]))
            state_color = set_color(
                data["df"], disk_info["disk_warn"], disk_info["disk_crit"]
            )
            draw.text((x, y), Disk, font=font, fill=state_color)
            y += font.getbbox(Disk)[3]

            Net = "Net: " + str(size(data["net_data"])) + "/" + str(net_speed) + "M"
            state_color = set_color(data["net_data"] / 1000, (net_speed * 0.80), net_speed)
            draw.text((x, y), Net, font=font, fill=state_color)
            y += font.getbbox(Net)[3]

            Procs = "Procs: " + str(data["procs"])
            state_color = set_color(
                data["procs"], procs_info["procs_warn"], procs_info["procs_crit"]
            )
            draw.text((x, y), Procs, font=font, fill=state_color)
            y += font.getbbox(Procs)[3]

            # y += 8
            # draw.line([(x, y), (width-padding, y)], default_color, 2)
            # y += 4

            # cmd = "dmesg --level=err,warn | tail -1"
            # Dmesg = subprocess.check_output(cmd, shell=True).decode("utf-8")
            # size_x = font.getbbox(Dmesg)[2]
            # text_x = lcd.width
            # x2 = (time.time() - t_start) * 100
            # x2 %= size_x + width
            # draw.text((int(text_x - x2), y), Dmesg, font=font, fill=(255, 255, 255))
            
            lcd.display(img)
            time.sleep(0.5)
        while not show_stats and num_iterations > 0 and signal_handler.can_run():
            with regulator:
                num_iterations -= 1
                frame_count += 1
                drawss.rectangle((0, 0, width, height), outline=outline_color, fill=fill_color)
                for s in ss:
                    s.update_pos()
                    s.draw()
                lcd.display(imgss)
    # Ensure exit if shutdown requested
    if signal_handler.shutdown_requested:
        sys.exit(0)


if __name__ == "__main__":
    signal_handler = SignalHandler()
    try:
        main()
    except KeyboardInterrupt:
        signal_handler.request_shutdown(signal.SIGINT, None)
