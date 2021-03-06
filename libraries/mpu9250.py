from __future__ import print_function
from __future__ import division

try:
	#~ import smbus2 as smbus
	import smbus2 as smbus
except ImportError:
	print('WARNING: Using fake hardware')
	from .fakeHW import smbus
	# from fake_rpi import smbus

from time import sleep
import struct
import ctypes # for signed int

import asyncio
from smbus2_asyncio import SMBus2Asyncio
import operator

# Todo:
# - replace all read* with the block read?

################################
# MPU9250
################################
MPU9250_ADDRESS = 0x69
AK8963_ADDRESS  = 0x0C
DEVICE_ID       = 0x71
WHO_AM_I        = 0x75
PWR_MGMT_1      = 0x6B
INT_PIN_CFG     = 0x37
INT_ENABLE      = 0x38
# --- Accel ------------------
ACCEL_DATA    = 0x3B
ACCEL_CONFIG  = 0x1C
ACCEL_CONFIG2 = 0x1D
ACCEL_2G      = 0x00
ACCEL_4G      = (0x01 << 3)
ACCEL_8G      = (0x02 << 3)
ACCEL_16G     = (0x03 << 3)
# --- Temp --------------------
TEMP_DATA = 0x41
# --- Gyro --------------------
GYRO_DATA    = 0x43
GYRO_CONFIG  = 0x1B
GYRO_250DPS  = 0x00
GYRO_500DPS  = (0x01 << 3)
GYRO_1000DPS = (0x02 << 3)
GYRO_2000DPS = (0x03 << 3)

# --- AK8963 ------------------
MAGNET_DATA  = 0x03
AK_DEVICE_ID = 0x48
AK_WHO_AM_I  = 0x00
AK8963_8HZ   = 0x02
AK8963_100HZ = 0x06
AK8963_14BIT = 0x00
AK8963_16BIT = (0x01 << 4)
AK8963_CNTL1 = 0x0A
AK8963_CNTL2 = 0x0B
AK8963_ASAX  = 0x10
AK8963_ST1   = 0x02
AK8963_ST2   = 0x09
AK8963_ASTC  = 0x0C
ASTC_SELF    = 0x01<<6

class mpu9250(object):
	def __init__(self, asyncLoop, bus=1):
		"""
		Setup the IMU

		reg 0x25: SAMPLE_RATE= Internal_Sample_Rate / (1 + SMPLRT_DIV)
		reg 0x29: [2:0] A_DLPFCFG Accelerometer low pass filter setting
			ACCEL_FCHOICE 1
			A_DLPF_CFG 4
			gives BW of 20 Hz
		reg 0x35: FIFO disabled default - not sure i want this ... just give me current reading

		might include an interface where you can change these with a dictionary:
			setup = {
				ACCEL_CONFIG: ACCEL_4G,
				GYRO_CONFIG: AK8963_14BIT | AK8963_100HZ
			}
		"""
		self.bus = smbus.SMBus(bus)
		self.busObj = SMBus2Asyncio(bus, loop=asyncLoop)
		self.busObj.open_sync()

		# let's double check we have the correct device address
		ret = self.read8(MPU9250_ADDRESS, WHO_AM_I)
		if ret is not DEVICE_ID:
			raise Exception('MPU9250: init failed to find device')

		self.write(MPU9250_ADDRESS, PWR_MGMT_1, 0x00)  # turn sleep mode off
		sleep(0.2)
		self.bus.write_byte_data(MPU9250_ADDRESS, PWR_MGMT_1, 0x01)  # auto select clock source
		self.write(MPU9250_ADDRESS, ACCEL_CONFIG, ACCEL_4G) # Gravity Limit
		self.write(MPU9250_ADDRESS, GYRO_CONFIG, GYRO_500DPS)
		configRegister = self.read8(MPU9250_ADDRESS, 0x26)
		configRegister = configRegister | 0x03 # Gyroscope cut-off at 184 Hz
		self.write(MPU9250_ADDRESS, 0x26,  configRegister)
		self.write(MPU9250_ADDRESS, 0x29, 0x01) # Accelerometer cut-off at 41 Hz

		# You have to enable the other chips to join the I2C network
		# then you should see 0x68 and 0x0c from:
		# sudo i2cdetect -y 1
		self.write(MPU9250_ADDRESS, INT_PIN_CFG, 0x22)
		self.write(MPU9250_ADDRESS, INT_ENABLE, 0x01)
		sleep(0.1)

		ret = self.read8(AK8963_ADDRESS, AK_WHO_AM_I)
		if ret is not AK_DEVICE_ID:
			raise Exception('AK8963: init failed to find device')
		self.write(AK8963_ADDRESS, AK8963_CNTL1, (AK8963_16BIT | AK8963_8HZ)) # cont mode 1
		self.write(AK8963_ADDRESS, AK8963_ASTC, 0)
		
		#normalization coefficients 
		self.alsb = 4.0 / 32760 # ACCEL_2G
		self.glsb = 500.0 / 32760 # GYRO_250DPS
		self.mlsb = 4912.0 / 32760 # MAGNET range +-4800

		# i think i can do this???
		# self.convv = struct.Struct('<hhh')

	def __del__(self):
		self.bus.close()

	def write(self, address, register, value):
		self.bus.write_byte_data(address, register, value)

	def read8(self, address, register):
		return self.bus.read_byte_data(address, register)

	def read16(self, address, register):
		return self.bus.read_i2c_block_data(address, register, 2)

	def read_xyz(self, address, register, lsb):
		"""
		Reads x, y, and z axes at once and turns them into a tuple.
		"""
		# data is MSB, LSB, MSB, LSB ...
		data = self.bus.read_i2c_block_data(address, register, 6)

		# data = []
		# for i in range(6):
		# 	data.append(self.read8(address, register + i))

		x = self.conv(data[0], data[1]) * lsb
		y = self.conv(data[2], data[3]) * lsb
		z = self.conv(data[4], data[5]) * lsb

		#print('>> data', data)
		# ans = self.convv.unpack(*data)
		# ans = struct.unpack('<hhh', data)[0]
		# print('func', x, y, z)
		# print('struct', ans)

		return (x, y, z)


	def read_mag_xyz(self, address, register, lsb):
		"""
		Reads x, y, and z axes at once and turns them into a tuple.
		"""
		# data is LSB, MSB, LSB, MSB ...
		data = self.bus.read_i2c_block_data(address, register, 6)

		# data = []
		# for i in range(6):
		# 	data.append(self.read8(address, register + i))

		x = self.conv(data[1], data[0]) * lsb
		y = self.conv(data[3], data[2]) * lsb
		z = self.conv(data[5], data[4]) * lsb

		#print('>> data', data)
		# ans = self.convv.unpack(*data)
		# ans = struct.unpack('<hhh', data)[0]
		# print('func', x, y, z)
		# print('struct', ans)

		return (x, y, z)

	def read_xyz_universal(self, address, register, lsb):
		"""
		Reads x, y, and z axes at once and turns them into a tuple.
		"""
		data = self.bus.read_i2c_block_data(address, register, 6)

		if register != 0x03:
			x = self.conv(data[0], data[1]) * lsb
			y = self.conv(data[2], data[3]) * lsb
			z = self.conv(data[4], data[5]) * lsb
		else:
			x = self.conv(data[1], data[0]) * lsb
			y = self.conv(data[3], data[2]) * lsb
			z = self.conv(data[5], data[4]) * lsb
			self.read8(AK8963_ADDRESS,AK8963_ST2) # needed step for reading magnetic data
		return [x, y, z]

	def conv(self, msb, lsb):
		value = lsb | (msb << 8)
		
		return ctypes.c_short(value).value

	@property
	def allSensors(self):

		[data, gyroData, magData] = map(lambda x, y, z: self.read_xyz_universal(x, y, z), [MPU9250_ADDRESS, MPU9250_ADDRESS, AK8963_ADDRESS], [ACCEL_DATA, GYRO_DATA, MAGNET_DATA], [self.alsb, self.glsb, self.mlsb])
		data.extend(gyroData)
		data.extend(magData)

		#~ [accData, gyroData, magData] = map(lambda x, y: self.bus.read_i2c_block_data(x, y, 6), [MPU9250_ADDRESS, MPU9250_ADDRESS, AK8963_ADDRESS], [ACCEL_DATA, GYRO_DATA, MAGNET_DATA])

		#~ data = [ctypes.c_short(accData[0] << 8 | accData[1]).value,   ctypes.c_short(accData[2] << 8 | accData[3]).value,   ctypes.c_short(accData[4] << 8 | accData[5]).value, 
				#~ ctypes.c_short(gyroData[0] << 8 | gyroData[1]).value, ctypes.c_short(gyroData[2] << 8 | gyroData[3]).value, ctypes.c_short(gyroData[4] << 8 | gyroData[5]).value,
				#~ ctypes.c_short(magData[1] << 8 | magData[0]).value,   ctypes.c_short(magData[3] << 8 | magData[2]).value,   ctypes.c_short(magData[5] << 8 | magData[4]).value]
		
		#~ lsb = [self.alsb, self.alsb, self.alsb, self.glsb, self.glsb, self.glsb, self.mlsb, self.mlsb, self.mlsb]

		#~ data = list(map(operator.mul, data, lsb))
		
		#~ accData = self.bus.read_i2c_block_data(MPU9250_ADDRESS, ACCEL_DATA, 6)
		#~ gyroData = self.bus.read_i2c_block_data(MPU9250_ADDRESS, GYRO_DATA, 6)
		#~ magData = self.bus.read_i2c_block_data(MPU9250_ADDRESS, MAGNET_DATA, 6)
		
		#~ data = [ctypes.c_short(accData[0] << 8 | accData[1]).value,   ctypes.c_short(accData[2] << 8 | accData[3]).value,   ctypes.c_short(accData[4] << 8 | accData[5]).value, 
				#~ ctypes.c_short(gyroData[0] << 8 | gyroData[1]).value, ctypes.c_short(gyroData[2] << 8 | gyroData[3]).value, ctypes.c_short(gyroData[4] << 8 | gyroData[5]).value,
				#~ ctypes.c_short(magData[1] << 8 | magData[0]).value,   ctypes.c_short(magData[3] << 8 | magData[2]).value,   ctypes.c_short(magData[5] << 8 | magData[4]).value]
		
		#~ lsb = [self.alsb]*3 + [self.glsb]*3 + [self.mlsb]*3 
		
		#~ data = [a*b for a,b in zip(lsb, data)]
		
		return data
		
	@property
	def accel(self):
		return self.read_xyz(MPU9250_ADDRESS, ACCEL_DATA, self.alsb)

	@property
	def gyro(self):
		return self.read_xyz(MPU9250_ADDRESS, GYRO_DATA, self.glsb)

	@property
	def temp(self):
		"""
		Returns chip temperature in C

		pg 33 reg datasheet:
		pg 12 mpu datasheet:
		Temp_room 21
		Temp_Sensitivity 333.87
		Temp_degC = ((Temp_out - Temp_room)/Temp_Sensitivity) + 21 degC
		"""
		temp_out = self.read16(MPU9250_ADDRESS, TEMP_DATA)
		temp = ((temp_out -21.0)/ 333.87)+21.0 # these are from the datasheets
		
		return temp

	@property
	def mag(self):
		data=self.read_mag_xyz(AK8963_ADDRESS, MAGNET_DATA, self.mlsb)
		self.read8(AK8963_ADDRESS,AK8963_ST2) # needed step for reading magnetic data
		
		return data

	@asyncio.coroutine
	def i2c_read8(self, register):
		"""Read a byte from I2C."""
		return (yield from self.busObj.read_byte_data(MPU9250_ADDRESS, int(register)))

	def i2c_write8(self, register, value):
		"""Write a byte to I2C."""
		Util.ensure_future(self.busObj.write_byte_data(MPU9250_ADDRESS, int(register), int(value)), loop=self.loop)
		# this does not return

	@asyncio.coroutine
	def i2c_read_block(self, register, count):
		"""Read a block from I2C."""
		return (yield from self.busObj.read_i2c_block_data(MPU9250_ADDRESS, int(register), int(count)))

	@asyncio.coroutine
	def i2c_allSensors(self):

		accData, gyroData, magData = yield from asyncio.gather(*(self.i2c_read_block(item, 6) for item in [ACCEL_DATA, GYRO_DATA, MAGNET_DATA]))
		#~ gyroData = await i2c_read_block(GYRO_DATA, 6)
		#~ magData = await i2c_read_block(MAGNET_DATA, 6)
		
		data = [ctypes.c_short(accData[0] << 8 | accData[1]).value,   ctypes.c_short(accData[2] << 8 | accData[3]).value,   ctypes.c_short(accData[4] << 8 | accData[5]).value, 
				ctypes.c_short(gyroData[0] << 8 | gyroData[1]).value, ctypes.c_short(gyroData[2] << 8 | gyroData[3]).value, ctypes.c_short(gyroData[4] << 8 | gyroData[5]).value,
				ctypes.c_short(magData[1] << 8 | magData[0]).value,   ctypes.c_short(magData[3] << 8 | magData[2]).value,   ctypes.c_short(magData[5] << 8 | magData[4]).value]
		
		lsb = [self.alsb]*3 + [self.glsb]*3 + [self.mlsb]*3 
		
		data = [a*b for a,b in zip(lsb, data)]
		
		return data
		
#~ async def i2c_read8(smBusObj, register):
	#~ """Read a byte from I2C."""
	#~ return await smBusObj.read_byte_data(MPU9250_ADDRESS, int(register))

#~ async def i2c_write8(smBusObj, register, value):
	#~ """Write a byte to I2C."""
	#~ Util.ensure_future(smBusObj.write_byte_data(MPU9250_ADDRESS, int(register), int(value)), loop=self.loop)
	#~ # this does not return

#~ async def i2c_read_block(smBusObj, register, count):
	#~ """Read a block from I2C."""
	#~ return await smBusObj.read_i2c_block_data(MPU9250_ADDRESS, int(register), int(count))

	
#~ async def i2c_allSensors(smBusObj):

		#~ accData, gyroData, magData = await asyncio.gather(*(i2c_read_block(smBusObj, item, 6) for item in [ACCEL_DATA, GYRO_DATA, MAGNET_DATA]))
		#~ gyroData = await i2c_read_block(GYRO_DATA, 6)
		#~ magData = await i2c_read_block(MAGNET_DATA, 6)
		
		#~ data = [ctypes.c_short(accData[0] << 8 | accData[1]).value,   ctypes.c_short(accData[2] << 8 | accData[3]).value,   ctypes.c_short(accData[4] << 8 | accData[5]).value, 
				#~ ctypes.c_short(gyroData[0] << 8 | gyroData[1]).value, ctypes.c_short(gyroData[2] << 8 | gyroData[3]).value, ctypes.c_short(gyroData[4] << 8 | gyroData[5]).value,
				#~ ctypes.c_short(magData[1] << 8 | magData[0]).value,   ctypes.c_short(magData[3] << 8 | magData[2]).value,   ctypes.c_short(magData[5] << 8 | magData[4]).value]
		
		#~ lsb = [self.alsb]*3 + [self.glsb]*3 + [self.mlsb]*3 
		
		#~ data = [a*b for a,b in zip(lsb, data)]
		
		#~ return data
