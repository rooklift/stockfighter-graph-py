# Realtime grapher for a Stockfighter venue
# Requires Python 3, and modules:
#
# PyGame: http://www.pygame.org/
# Requests: http://docs.python-requests.org/
# Websocket Client: http://pypi.python.org/pypi/websocket-client/

import json, queue, sys, threading, time
import pygame; from pygame.locals import *
import requests
from websocket import create_connection

INSTRUCTIONS = '''Realtime Stockfighter venue grapher
by Fohristiwhirl a.k.a. Amtiskaw

Instructions:
Change zoom with mouse wheel.
Drag graph by holding the mouse button.

Blue  : bid
Red   : ask
White : trades
'''

API_URL = "https://api.stockfighter.io/ob/api/"
WS_URL = "wss://api.stockfighter.io/ob/api/ws/"


class Frame():															# Info about 1 quote
	def __init__(self, trade = None, bid = None, ask = None):
		if trade is not None:
			self.trade = Point(trade, 255, 255, 255, large = True)
		else:
			self.trade = None
		
		if bid is not None:
			self.bid = Point(bid, 0, 180, 255)
		else:
			self.bid = None
		
		if ask is not None:
			self.ask = Point(ask, 255, 0, 0)
		else:
			self.ask = None


class Point():
	def __init__(self, price, r, g, b, large = False):
		self.price = price
		self.r = r
		self.g = g
		self.b = b
		self.color = pygame.Color(r, g, b)
		self.large = large


class Application ():
	def __init__(self, venue, symbol, width, height, y_scale):
		
		self.width = width
		self.height = height
		
		self.y_scale = y_scale
		self.mid_y = 7500
	
		pygame.init()
		self.screen = pygame.display.set_mode((self.width, self.height))
		self.fpsClock = pygame.time.Clock()
		
		self.data = Data(venue, symbol, self.width)
		self.devices = Devices()
	
	def cls(self):
		self.screen.fill(pygame.Color(0,0,0))
	
	def get_screen_y_from_price(self, price):
		return int(self.height / 2 - ((price - self.mid_y) * self.y_scale))
	
	def get_price_from_screen_y(self, screen_y):
		return int(self.mid_y - ((screen_y - self.height / 2) / self.y_scale))
	
	def draw_point(self, x, point):
		y = self.get_screen_y_from_price(point.price)
		self.screen.set_at((x, y), point.color)
		if point.large:
			self.screen.set_at((x, y + 1), point.color)
			self.screen.set_at((x, y - 1), point.color)
	
	def draw_frames(self):
		ls = self.data.all_frames
		if ls:
			x = self.width
			for frame in reversed(ls):
				x -= 1
				if x < 0:
					break
				if frame.bid:
					self.draw_point(x, frame.bid)
				if frame.ask:
					self.draw_point(x, frame.ask)
				if frame.trade:
					self.draw_point(x, frame.trade)
	
	def flip(self):
		pygame.display.update()
	
	def handle_inputs(self):
		if self.devices.button:
			self.mid_y += self.devices.y_movement / self.y_scale
		
		if self.devices.mwheel_rolled_up:
			self.y_scale *= 1.2
			self.set_caption()

		if self.devices.mwheel_rolled_down:
			self.y_scale /= 1.2
			self.set_caption()
		
		if self.devices.y_movement:
			self.set_caption()
	
	def set_caption(self):
		pygame.display.set_caption("{} {} --- Last: {}  (Mouse: {})".format(
									self.data.venue, self.data.symbol, self.data.last_price, self.get_price_from_screen_y(self.devices.mousey)))
	
	def run(self):
		self.set_caption()
		while 1:
			self.devices.update_state()
			self.handle_inputs()
			
			self.data.update()
			self.set_caption()
			
			self.cls()
			self.draw_frames()
			self.flip()

			self.fpsClock.tick(60)


class Devices ():
	def __init__(self):
		# Long lasting traits:
		self.mousex = 0
		self.mousey = 0
		self.button = False
		self.keysdown = set()
		# Single tick traits:
		self.x_movement = 0
		self.y_movement = 0
		self.mwheel_rolled_down = False
		self.mwheel_rolled_up = False
	
	def update_state(self):
		self.x_movement = 0
		self.y_movement = 0
		self.mwheel_rolled_down = False
		self.mwheel_rolled_up = False
	
		for event in pygame.event.get():
		
			if event.type == QUIT:
				pygame.quit()
				sys.exit()
			
			elif event.type == MOUSEMOTION:
				oldx, oldy = self.mousex, self.mousey
				self.mousex, self.mousey = event.pos
				self.x_movement += self.mousex - oldx
				self.y_movement += self.mousey - oldy
			
			elif event.type == MOUSEBUTTONDOWN:
				if event.button == 1:													# Left-click
					self.button = True
				elif event.button == 4:													# Scroll wheel up
					self.mwheel_rolled_up = True
				elif event.button == 5:													# Scroll wheel down
					self.mwheel_rolled_down = True
	
			elif event.type == MOUSEBUTTONUP:
				if event.button == 1:
					self.button = False
	
			elif event.type == KEYDOWN:
				k = event.key
				self.keysdown.add(k)
			
			elif event.type == KEYUP:
				k = event.key
				self.keysdown.discard(k)


class Data ():
	def __init__(self, venue, symbol, width):
		self.venue = venue
		self.symbol = symbol
		self.all_frames = []
		self.width = width
		self.last_trade_time = None
		self.last_price = None
		self.tick_queue = queue.Queue()
		self.total_updates = 0
		self.start_ticker()
	
	def start_ticker(self):
		newthread = threading.Thread(target = ticker_thread, daemon = True, kwargs = {
										"venue"			: self.venue,
										"symbol"		: self.symbol,
										"output_queue"	: self.tick_queue}
									)
		newthread.start()
	
	def update(self):
		
		# Occasionally clear the all_frames list of old stuff...
		
		self.total_updates += 1
		if self.total_updates % 100 == 0 and len(self.all_frames) > self.width:
			self.all_frames = self.all_frames[-self.width:]
		
		while 1:
		
			trade, bid, ask = None, None, None
		
			try:
				quote = self.tick_queue.get(block = False)
			except queue.Empty:
				return
			
			if quote:
				try:
					if quote["quote"]["lastTrade"] != self.last_trade_time:					# Trades (added only if not seen before)
						self.last_trade_time = quote["quote"]["lastTrade"]
						self.last_price = quote["quote"]["last"]
						trade = self.last_price
				except KeyError:
					pass
				try:
					bid = quote["quote"]["bid"]												# Bids
				except KeyError:
					pass
				try:
					ask = quote["quote"]["ask"]												# Asks
				except KeyError:
					pass
			
			self.all_frames.append(Frame(trade, bid, ask))

def ticker_thread(venue, symbol, output_queue = None, verbose = False):

	account = "HOGEDOGE"					# I think account isn't validated, anything works?
	
	url = WS_URL + "{}/venues/{}/tickertape/stocks/{}".format(account, venue, symbol)
	ws = create_connection(url)
	
	while 1:

		try:
			raw_food = ws.recv()
		except:
			ws = create_connection(url)
			continue
		
		if verbose:
			print(raw_food)
		
		if output_queue:
			try:
				result = json.loads(raw_food)
				output_queue.put(result)
				continue
			except:
				continue

def get_json_from_url(url):
	try:
		r = requests.get(url)
	except TimeoutError:
		print("TIMED OUT WAITING FOR REPLY (REQUEST MAY STILL HAVE SUCCEEDED).")
		return None
	except requests.exceptions.ConnectionError:
		print("TIMED OUT WAITING FOR REPLY (REQUEST MAY STILL HAVE SUCCEEDED).")
		return None
	
	# We got some sort of reply...
	
	try:
		result = r.json()
	except ValueError:
		print(r.text)
		print("RESULT WAS NOT VALID JSON.")
		return None
	
	# The reply was valid JSON...
	
	if "ok" not in result:
		print(r.text)
		print("THE 'ok' FIELD WAS NOT PRESENT.")
		return None
	if result["ok"] != True:
		print(r.text)
		print("THE 'ok' FIELD WAS NOT TRUE.")
		return None
	
	return result

def liststocks(venue):
	return get_json_from_url(API_URL + "venues/{}/stocks".format(venue))

if __name__ == "__main__":
	print(INSTRUCTIONS)

	venue = input("Venue? ")
	r = liststocks(venue)
	if r:
		symbol = r["symbols"][0]["symbol"]
		app = Application(venue, symbol, width = 1800, height = 600, y_scale = 0.04)
		app.run()
	else:
		input()
