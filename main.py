#!/usr/bin/env python3

import warnings
warnings.filterwarnings("ignore")

import os
import signal
import socket
# import subprocess
import sys
import threading
import time
import paho.mqtt.client as mqtt
import psutil
import requests
from hurry.filesize import size
import luma.core.render
from luma.core.interface.serial import spi
from luma.lcd.device import st7789
from luma.core.sprite_system import framerate_regulator
from PIL import Image, ImageDraw, ImageFont
import random

graphite_url = os.getenv("GRAPHITE_URL")
metric_group = os.getenv("METRIC_GROUP")
mqtt_server = os.getenv("MQTT_SERVER")
motion_topic = os.getenv("MOTION_TOPIC")
lcd_rotation = int(os.getenv("LCD_ROTATION", 1))

serial = spi(spi_mode=3, port=0, device=0, gpio_DC=17, gpio_RST=22, bus_speed_hz=8000000)
lcd = st7789(serial, rotate=lcd_rotation)
width = lcd.width
height = lcd.height

default_color = "#FFFFFF"
logo = Image.open("raspberrypi.png")
img = Image.new("RGB", (width, height), color=(0, 0, 0))
draw = ImageDraw.Draw(img)
imgss = Image.new("RGB", (width, height), color=(0, 0, 0))
drawss = ImageDraw.Draw(imgss)

# Boot splashscreen
draw.rectangle((0, 0, width, height), outline=0, fill=0)
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
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

padding = -2
top = padding
bottom = height - padding
global x
x = 0
data = {}
show_stats = True


class screensaver(object):
    def __init__(self, w, h, image):
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
        # drawss.bitmap((int(self._x_pos),int(self._y_pos)), logo, )
        

class SignalHandler:
    shutdown_requested = False

    def __init__(self):
        signal.signal(signal.SIGINT, self.request_shutdown)
        signal.signal(signal.SIGTERM, self.request_shutdown)

    def request_shutdown(self, *args):
        print('Request to shutdown received, stopping')
        lcd.cleanup()
        self.shutdown_requested = True

    def can_run(self):
        return not self.shutdown_requested

def on_connect(client, userdata, flags, rc):
    print("MQTT Connected with result code "+str(rc))
    client.subscribe(motion_topic)

def on_message(client, userdata, msg,):
    global show_stats
    if "motion" in msg.topic:
        if str(msg.payload.decode("utf-8")) == "1":
            client.publish(hostname + "/stats_display", "1")
            show_stats = True
        if str(msg.payload.decode("utf-8")) == "0":
            show_stats = False
            client.publish(hostname + "/stats_display", "1")

def set_color(value, warn, crit):
    if warn != None and crit != None:
        if value >= crit:
            return "#FF0000"
        elif value >= warn:
            return "#FFFF00"
        else:
            return "#00FF00"
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
    request_url = (
        graphite_url
        + "render/?"
        + "target=summarize("
        + metric_group
        + "."
        + hostname
        + ".services."
        + metric_name
        + "."
        + metric_path
        + "."
        + metric_value
        + ",'1hour','last')&from=-1h&format=json"
    )
    r = requests.get(request_url, verify=False)
    try:
        result = r.json()[0]["datapoints"][-1][0]
        return result
    except:
        return ""

def get_info(service, path):
    service_name = service.split(".")[0]
    if graphite_url != None:
        info = {
            service_name + "_crit": get_value(service, "crit", path),
            service_name + "_warn": get_value(service, "warn", path),
            service_name + "_max": get_value(service, "max", path),
        }
    else:
        info = {
            service_name + "_crit": None,
            service_name + "_warn": None,
            service_name + "_max": None,
        }   
    return info

def get_data():
    global hostname, ip, net_speed, cpu_freq_max, load_info, temp_info, disk_info, mem_info, procs_info

    # hostname = socket.gethostname()
    hostname = "rpi-node1"

    try:
        net_speed = psutil.net_if_stats()["br0"].speed
        ip = psutil.net_if_addrs()["br0"][0].address
    except:
        try:
            net_speed = psutil.net_if_stats()["eth0"].speed
            ip = psutil.net_if_addrs()["eth0"][0].address
        except:
            net_speed = psutil.net_if_stats()["wlan0"].speed
            ip = psutil.net_if_addrs()["wlan0"][0].address

    cpu_freq_max = cpu_clock = psutil.cpu_freq().max

    load_info = get_info("load.load", "perfdata.load5")
    temp_info = get_info("temperature.check_rpi_temp_py", "perfdata.rpi_temp")
    disk_info = get_info("disk.disk", "perfdata._")
    mem_info = get_info("mem.mem", "perfdata.USED")
    procs_info = get_info("procs.procs", "perfdata.procs")

    if graphite_url != None:
        print("Using Graphite data")

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

    # signal.signal(signal.SIGTERM, signal_term_handler)
    signal_handler = SignalHandler()
    x = 0

    thread_get = threading.Thread(target=get_data, daemon=True)
    thread_get.start()

    thread_update = threading.Thread(target=update_data)
    thread_update.start()

    frame_count = 0
    # fps = ""
    # canvas = luma.core.render.canvas(lcd, dither=True)
    regulator = framerate_regulator(fps=0)

    time.sleep(10)

    if mqtt_server != None:
        client = mqtt.Client(socket.gethostname() + "_stats_display")
        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(mqtt_server)
        client.loop_start()

    global t_start
    t_start = time.time()

    # ss = screensaver(width, height)
    ss = [screensaver(width, height, i) for i in [logo]]

    while signal_handler.can_run():
        while show_stats:

            # img = Image.new("RGB", (width, height), color=(0, 0, 0))
            # draw = ImageDraw.Draw(img)
            draw.rectangle((0, 0, width, height), outline=0, fill=0)

            y = top

            Hostname = str(hostname)
            state_color = default_color
            draw.text((x, y), Hostname, font=font_big, fill=state_color)
            y += font.getbbox(Hostname)[3]

            Ip = str(ip)
            state_color = default_color
            draw.text((x, y), Ip, font=font_big, fill=state_color)
            y += font.getbbox(Ip)[3]

            y += 8
            draw.line([(x, y), (width, y)], default_color, 2)
            y += 4

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
                temp_info["temperature_warn"],
                temp_info["temperature_crit"],
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

            y += 8
            draw.line([(x, y), (width, y)], default_color, 2)
            y += 4

            # cmd = "dmesg --level=err,warn | tail -1"
            # Dmesg = subprocess.check_output(cmd, shell=True).decode("utf-8")
            # size_x = font.getbbox(Dmesg)[2]
            # text_x = lcd.width
            # x2 = (time.time() - t_start) * 100
            # x2 %= size_x + width
            # draw.text((int(text_x - x2), y), Dmesg, font=font, fill=(255, 255, 255))
            
            lcd.display(img)
            time.sleep(0.5)

        while not show_stats:
            while num_iterations > 0:
                with regulator:
                    num_iterations -= 1

                    frame_count += 1
                    # with canvas as c:
                    drawss.rectangle((0, 0, width, height), outline=0, fill=0)
                    for s in ss:
                        s.update_pos()
                        s.draw()
                    lcd.display(imgss)
                    # if frame_count % 20 == 0:
                    #     fps = "FPS: {0:0.3f}".format(regulator.effective_FPS())

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        lcd.cleanup()
        pass
