#!/usr/bin/env python3

import signal
import sys
import time
import threading
import subprocess
import socket
import psutil
from hurry.filesize import size
import paho.mqtt.client as mqtt
from PIL import Image, ImageDraw, ImageFont
import ST7789
import requests

from private import *

lcd = ST7789.ST7789(  height=240,
                      width=240,
                      port= 0,
                      cs = ST7789.BG_SPI_CS_FRONT,
                      dc = 17,
                      rst = 22,
                      backlight = 27,
                      mode = 3,
                      spi_speed_hz=80 * 1000 * 1000,)

lcd.begin()

WIDTH = lcd.width
HEIGHT = lcd.height

img = Image.new('RGB', (WIDTH, HEIGHT), color=(0, 0, 0))
draw = ImageDraw.Draw(img)
draw.rectangle((0, 0, WIDTH, HEIGHT), outline=0, fill=0)

font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)

draw.text((2, 2), "Raspberry Pi Stats", font=font_big, fill="#FFFFFF")
draw.text((2, 30), "Starting...", font=font_big, fill="#FFFFFF")

lcd.display(img)

PADDING = -2
TOP = PADDING
bottom = HEIGHT - PADDING

global x
x = 0

data = {}

def on_connect(client, userdata, flags, rc):
  # print("Connected with result code "+str(rc))
  client.subscribe(motion_topic)

def on_message(client, userdata, msg):
  if 'motion' in msg.topic:
    if str(msg.payload.decode("utf-8")) == '1':
    # lcd.set_backlight(True)
      client.publish(hostname + "/oled", "1")
    if str(msg.payload.decode("utf-8")) == '0':
    # lcd.set_backlight(False)
      client.publish(hostname + "/oled", "1")


client = mqtt.Client(socket.gethostname())

client.on_connect = on_connect
client.on_message = on_message

client.connect("mqtt.rpisrv.it")

def signal_term_handler(signal, frame):
    sys.exit(0)


signal.signal(signal.SIGTERM, signal_term_handler)


def set_color(value, warn, crit):
    if warn or crit is None:
        return "#FFFFFF"
    elif value >= crit:
        return "#FF0000"
    elif value >= warn:
        return "#FFFF00"
    else:
        return "#00FF00"


def first(iterable, default=None):
    if iterable:
        for item in iterable:
            return item
    return default


def intersect(a, b):
    return list(set(a) & set(b))


def get_value(metric_name, metric_value, metric_path):
    try:
        graphite_url
    except:
        return ""
    else:
      request_url = graphite_url + "render/?" + "target=summarize(" + metric_group + "." + \
                    hostname + ".services." + metric_name + "." + metric_path + "." + metric_value + ",'1hour','last')&from=-1h&format=json"
      r = requests.get(request_url)
    try:
        result = r.json()[0][u'datapoints'][-1][0]
        return result
    except Exception as e:
        return ""

def get_info(service, path):
    service_name = service.split(".")[0]
    info = {
      service_name + "_crit": get_value(service,'crit',path),
      service_name + "_warn": get_value(service,'warn',path),
      service_name + "_max": get_value(service,'max',path)
    }
    return info


def get_data():

    global hostname, \
              ip, \
              net_speed, \
              cpu_freq_max, \
              load_info, \
              temp_info, \
              disk_info, \
              mem_info, \
              procs_info

    hostname = socket.gethostname()
    # hostname = "rpi-node1"

    try:
      net_speed = psutil.net_if_stats()["br0"].speed
      ip = psutil.net_if_addrs()["br0"][0].address
    except:
      net_speed = psutil.net_if_stats()["wlan0"].speed
      ip = psutil.net_if_addrs()["wlan0"][0].address

    cpu_freq_max = cpu_clock = psutil.cpu_freq().max

    try:
      graphite_url
    except:
      load_info = {"load_warn": None, "load_crit": None}
      temp_info = {"temperature_warn": None, "temperature_crit": None}
      disk_info = {"disk_warn": None, "disk_crit": None}
      mem_info = {"mem_warn": None, "mem_crit": None}
      procs_info = {"procs_warn": None, "procs_crit": None}
      print("Graphite URL not defined")
    else:
      load_info = get_info("load.load",'perfdata.load5')
      temp_info = get_info("temperature.check_rpi_temp_py",'perfdata.rpi_temp')
      disk_info = get_info("disk.disk",'perfdata._')
      mem_info = get_info("mem.mem",'perfdata.USED')
      procs_info = get_info("procs.procs",'perfdata.procs')
      print("Data fetched")

    time.sleep(3600)


def update_data():
  while True:
    global old_net_value, new_net_value
    if 'old_net_value' not in globals():

        old_net_value = 0
    if 'new_net_value' not in globals():
        new_net_value = 0

    cpu_used = round(psutil.getloadavg()[1],1)
    cpu_clock = psutil.cpu_freq().current
    cpu_temp = round((list(psutil.sensors_temperatures().values())[0])[0].current, 1)
    mem = psutil.virtual_memory()
    mem_used = (mem.total - mem.available)
    df = psutil.disk_usage("/").used
    df_max = psutil.disk_usage("/").total

    # network_ifs = psutil.net_if_stats().keys()
    # wlan0 = first(intersect(network_ifs, ["wlan0", "wl0"]), "wlan0")
    # eth0 = first(intersect(network_ifs, ["eth0", "en0"]), "eth0")

    new_net_value = psutil.net_io_counters().bytes_sent + psutil.net_io_counters().bytes_recv
    net_data = new_net_value - old_net_value
    old_net_value = new_net_value

    procs = len([key for key in psutil.process_iter()])

    global data
    data = {'cpu_used': cpu_used,
            'cpu_clock': cpu_clock,
            'cpu_temp': cpu_temp,
            'mem_total': mem.total,
            'mem_used': mem_used,
            'df': df,
            'df_max': df_max,
            'net_data': net_data,
            'procs': procs}

    time.sleep(2)


def main():
  x = 0

  thread_get = threading.Thread(target=get_data, daemon=True)
  thread_get.start()

  thread_update = threading.Thread(target=update_data)
  thread_update.start()

  client.loop_start()

  time.sleep(10)

  t_start = time.time()

  while True:

    draw.rectangle((0, 0, WIDTH, HEIGHT), outline=0, fill=0)

    y = TOP

    Hostname = str(hostname)
    state_color = "#FFFFFF"
    draw.text((x, y), Hostname, font=font_big, fill=state_color)
    y += font.getbbox(Hostname)[3]

    Ip = str(ip)
    state_color = "#FFFFFF"
    draw.text((x, y), Ip, font=font_big, fill=state_color)
    y += font.getbbox(Ip)[3]

    y += 8
    draw.line([(x, y),(WIDTH,y)], "#FFFFFF", 2)
    y += 4

    Load = "Cpu Load: " + str(data['cpu_used'])
    state_color = set_color(data['cpu_used'], load_info['load_warn'], load_info['load_crit'])
    draw.text((x, y), Load, font=font, fill=state_color)
    y += font.getbbox(Load)[3]

    Clock = "Cpu Clock: " + str(data['cpu_clock'])
    state_color = set_color(data['cpu_clock'], (cpu_freq_max * 0.80), cpu_freq_max)
    draw.text((x, y), Clock, font=font, fill=state_color)
    y += font.getbbox(Clock)[3]

    Temp = "Temp: " + str(data['cpu_temp']) + "°C"
    state_color = set_color(data['cpu_temp'], temp_info['temperature_warn'], temp_info['temperature_crit'])
    draw.text((x, y), Temp, font=font, fill=state_color)
    y += font.getbbox(Temp)[3]

    Mem = "Mem: " + str(size(data['mem_used'])) + "/" + str(size(data['mem_total']))
    state_color = set_color(data['mem_used'], mem_info['mem_warn'], mem_info['mem_crit'])
    draw.text((x, y), Mem, font=font, fill=state_color)
    y += font.getbbox(Mem)[3]

    Disk = "Disk: " + str(size(data['df'])) + "/" + str(size(data['df_max']))
    state_color = set_color(data['df'], disk_info['disk_warn'], disk_info['disk_crit'])
    draw.text((x, y), Disk, font=font, fill=state_color)
    y += font.getbbox(Disk)[3]

    Net = "Net: " + str(size(data['net_data'])) + "/" + str(net_speed) + "M"
    state_color = set_color(data['net_data']/1000, (net_speed * 0.80), net_speed)
    draw.text((x, y), Net, font=font, fill=state_color)
    y += font.getbbox(Net)[3]

    Procs = "Procs: " + str(data['procs'])
    state_color = set_color(data['procs'], procs_info['procs_warn'], procs_info['procs_crit'])
    draw.text((x, y), Procs, font=font, fill=state_color)
    y += font.getbbox(Procs)[3]

    y += 8
    draw.line([(x, y),(WIDTH,y)], "#FFFFFF", 2)
    y += 4

    cmd = "dmesg --level=err,warn | tail -1"
    Dmesg = subprocess.check_output(cmd, shell=True).decode("utf-8")
    size_x = font.getbbox(Dmesg)[2]
    text_x = lcd.width
    x2 = (time.time() - t_start) * 100
    x2 %= (size_x + WIDTH)
    draw.text((int(text_x - x2), y), Dmesg, font=font, fill=(255, 255, 255))

    lcd.display(img)

    time.sleep(0.5)

if __name__ == "__main__":
  try:
    main()
  except KeyboardInterrupt:
    draw.rectangle((0, 0, WIDTH, HEIGHT), outline=0, fill=0)
    lcd.display(img)
    pass