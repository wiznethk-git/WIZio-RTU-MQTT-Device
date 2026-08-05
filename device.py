import time
from machine import Pin, DAC, ADC, UART, SoftI2C, CAN
from eeprom import EEPROM
from validation import is_valid_can_id, is_valid_can_data
from debug import dprint

MAX_DO = const(8)
MAX_DI = const(8)
MAX_AO = const(2)
MAX_AI = const(4)

# ADC
ADC_MODE_VOLTAGE = const(0)
ADC_MODE_CURRENT = const(1)
ADC_PIN_PREDEF_MODE = [ADC_MODE_CURRENT,ADC_MODE_CURRENT, ADC_MODE_VOLTAGE, ADC_MODE_VOLTAGE]

# DAC
DAC_BITS = const(12)
MAX_DAC_WRITE_VALUE = const((1 << DAC_BITS) - 1)

# Used for calibration DAC. (Vmax and Vmin per channel)
# DAC0 still need some normalization
DAC_CONFIG = {
    0: (0.024, 9.89),
    1: (0.079, 9.88)
}

# UART and CAN default Configs
# Default uses CAN, import UART if not available
try:
    from config import CAN_USED , CAN_CONFIGS
except ImportError:
    CAN_USED = True
    CAN_CONFIGS = {}

if not CAN_USED:
    try:
        from config import UART_CONFIGS
    except ImportError:
        UART_CONFIGS = {}

# CAN
CAN_TIMEOUT = const(1000)
MAX_CAN_FRAMES = const(3)

# EEPROM
MAX_EEPROM_BYTES_PER_WRITE = const(1000)
MAX_EEPROM_BYTES_PER_READ = const(1000)
EEPROM_ADDRS = set(range(0x50, 0x58))

# 4G_MODULE (MODEM)
try:
    from config import MODEM_CONFIGS
except ImportError:
    MODEM_CONFIGS = {}

if MODEM_CONFIGS:
    from modem import ModemManager

# Extra




class Device:
    def __init__(self):
        self.din = [Pin(f'IN{idx + 1}', Pin.IN) for idx in range(MAX_DI)]
        self.dout = [Pin(f'RY{idx + 1}', Pin.OUT) for idx in range(MAX_DO)]
        self.analog_in = []
        for idx in range(MAX_AI):
            pin = ADC(Pin(f'AI{idx}'))
            mode = ADC_PIN_PREDEF_MODE[idx]
            self.analog_in.append((pin, mode))
        self.analog_out = [DAC(Pin(f'AO{idx}'), bits = DAC_BITS) for idx in range(MAX_AO)]
        
        # UART/CAN for Pin(PB9, PB8)
        self.serial = None
        self.can_bus = None
        dprint(f"Initing CAN_USED: {CAN_USED}")
        if not CAN_USED:
            self.uart_baudrate = UART_CONFIGS.get('baudrate', 9600)
            self.uart_bits = UART_CONFIGS.get('bits', 8)
            self.uart_parity = UART_CONFIGS.get('parity', None)
            self.uart_stop = UART_CONFIGS.get('stop', 1)
            self.serial = UART(
                3,
                self.uart_baudrate,
                self.uart_bits,
                self.uart_parity,
                self.uart_stop,
            )
        else:
            self.can_baudrate = CAN_CONFIGS.get('baudrate', 500000)
            self.can_mode = CAN_CONFIGS.get('mode', CAN.NORMAL)
            self.can_bus = CAN(
                1,
                baudrate = self.can_baudrate,
                mode = self.can_mode,
                rx = Pin("PB8"),
                tx = Pin("PB9"),
            )
#             self.can_rstart = 0
#             self.can_rend = 0
#             self.can_rcount = 0
#             self.can_ringbuf = [bytearray(32) for _ in range(MAX_CAN_FRAMES)]
                
            
        # I2C, can have more than 1 I2C device
        self.i2c = SoftI2C(scl=Pin('PB6'), sda=Pin('PB7') )  
        
        #EEPROM
        self.eeprom = None
        if self._is_eeprom_connected():
            self.eeprom = EEPROM(
                pages = 128,
                bpp = 16,
                i2c = self.i2c
            )
        # 4G
        self.modem = None
        if MODEM_CONFIGS:
             self.modem = ModemManager(
                MODEM_CONFIGS.get("baudrate", 115200),
                MODEM_CONFIGS.get("bits", 8),
                MODEM_CONFIGS.get("parity", None),
                MODEM_CONFIGS.get("stop", 1)     
             )
        
        
    # Digital I/O
    def read_din_at(self, idx = 0):
        try:
            return self.din[idx].value()
        except IndexError:
            print('Index out of range.')
            return -1
        except TypeError:
            print('idx must be an integer.')
            return -1
            
    def write_dout_at(self, idx = 0, value = 0):
        try:
            pin_value = self.dout[idx].value(value)
            return pin_value
        except IndexError:
            print('Index out of range.')
            return -1
        except TypeError:
            print('idx must be an integer.')
            return -1
    
    def read_dout_at(self, idx = 0):
        try:
            return self.dout[idx].value()
        except IndexError:
            print('Index out of range.')
            return -1
        except TypeError:
            print('idx must be an integer.')
            return -1
        
    def write_dout(self, pin, value):
        try:
            pin = Pin(pin, Pin.OUT)
            pin.value(value)
            return pin.value()
        except ValueError:
            print(f'No pin {pin}')
            return -1
    
    # Analog I/O
    def write_aout_at(self, idx, voltage):
        try:
            pin = self.analog_out[idx]
            Vmin, Vmax = DAC_CONFIG.get(idx, 0) 
            # Calibration with measured values
            steps_needed = int((voltage - Vmin)* MAX_DAC_WRITE_VALUE / (Vmax-Vmin))  
            if steps_needed > MAX_DAC_WRITE_VALUE:
                steps_needed = MAX_DAC_WRITE_VALUE
            elif steps_needed < 0:
                steps_needed = 0
           
            print(f'{voltage} used {steps_needed}.')
            pin.write(steps_needed)
            
            # Old
#             volts_per_step = 9.9 / ((1 << 12) - 1)
#             steps_needed = int(voltage / volts_per_step)
#             if steps_needed > (1 << 12) -  1:
#                 steps_needed = (1 << 12) - 1
#             pin.write(steps_needed)
            return True
        except IndexError:
            print(f'Wrong index for AO: {idx}')
            return -1
    
    def write_aout(self, pin_name, voltage):
        try:
            pin = DAC(pin_name)
            
            # Possible pin_name can only be AO0/1 and PA4/5
            if pin_name.startswith('AO'):
                idx = int(pin_name[-1])
            else:
                idx = 0 if int(pin_name[-1]) == 4 else 1 
                
            Vmin, Vmax = DAC_CONFIG.get(idx, 0) 
            # Calibration with measured values
            steps_needed = int((voltage - Vmin)* MAX_DAC_WRITE_VALUE / (Vmax -Vmin))  
            if steps_needed > MAX_DAC_WRITE_VALUE:
                steps_needed = MAX_DAC_WRITE_VALUE
            elif steps_needed < 0:
                steps_needed = 0
           
            print(f'{voltage} used {steps_needed}.')
            pin.write(steps_needed)
            return True
        except IndexError:
            print(f'Wrong index for AO: {idx}')
            return -1
    
    def read_ain_all(self):
        data = {}
        for idx, analog_in_with_mode in enumerate(self.analog_in):
            data[f'AI{idx}'] = {
                "value": self.read_ain_at(idx),
                "mode": analog_in_with_mode[1]
            }
        return data
    
    def read_ain_at(self, idx):
        try:
            pin, mode = self.analog_in[idx]
            voltage = (pin.read_u16() / 65535) * 3.3
            return self._calculate_adc_value(voltage, mode)
        except IndexError:
            print(f'Wrong index for AI: {idx}')
            return -1
    
    def _calculate_adc_value(self, voltage, mode = ADC_MODE_CURRENT):
        if mode == ADC_MODE_CURRENT:
            # V_measure = I_actual * 120R
            # Apparently, VADC0 is same as VAD0 if in current mode.
            # A to mA is * 1000
            return (voltage / 120) * 1000
        else:
            # V_measure = V_out * 4.7 / (10 + 4.7)
            # V_out can be calculated with read_u16 * Vmax_pin (3.3V) 
            return (voltage * 14.7) / 4.7
        
    # UART
    def uart_transmission(self, message):
        if not self.send_uart_message(message.encode()):
            return None
                
    def send_uart_message(self, message):
        if self.serial is None:
            return
        head = 0
        while head < len(message):
            mv = memoryview(message)[head:]
            num_bytes = self.serial.write(message)
            if not num_bytes:
                return False
            head += num_bytes
        return True
    
    def recv_uart_message(self):
        if self.serial is None:
            return
        if not self.serial.any() > 0: 
            return None
        data = bytearray()
        while self.serial.any():
            data.extend(self.serial.read())
        if data is not None: # DK why sometimes will be None
            try:
                message = data.decode('utf-8', 'replace')
                return message
            except UnicodeError as e:
                print('Decoding uart failed. Please check your UART port settings.')
                print('Raw: ', data)
        return None
    
    # CAN
    def send_can_frame(self, can_id , data):
        if not self.can_bus:
            print('Can bus not initialized')
            return False        
        if not isinstance(data, (bytes, bytearray)):
            try:
                data = data.encode()
            except Exception as e:
                print("Can't decode data: ", data)
                dprint(e)
                return False           
        if not (is_valid_can_id(can_id) and is_valid_can_data(data)):
            return False
        try:
            self.can_bus.send(data, can_id)
        except OSError as e:
            print('Error in can data send.')
            dprint(e)
            return False
        return True
            
    def recv_can_frames(self):
        if not self.can_bus:
            print('Can bus not initialized')
            return None
        
        if not self.can_bus.any():
            return None
        
        
#         return self.drain_can_ringbuf()
        frames = []
    
        while self.can_bus.any() and len(frames) < MAX_CAN_FRAMES:
            raw_frame = self.can_bus.recv(0, CAN_TIMEOUT)
            frame_data = {}
            frame_data["id"] = raw_frame[0]
            frame_data["data"] = raw_frame[1]
            frame_data["frame_type"] = raw_frame[2]
            frames.append(frame_data)
        return frames
            
    # I2C
    # EEPROM
    def _is_eeprom_connected(self):
        scan_addr = set(self.i2c.scan())
        if EEPROM_ADDRS.issubset(scan_addr):
            return True
        return False
            
    def eeprom_write(self, data, idx = 0):
        assert self.eeprom is not None
        if not isinstance(data, bytes):
            data = data.encode('utf-8')
        if len(data) > MAX_EEPROM_BYTES_PER_WRITE:
            print('Exceed max write capacity: ', MAX_EEPROM_BYTES_PER_WRITE)
            return -1
        try:
            self.eeprom.write(idx, data)
        except ValueError as e:
            print(e)
            return -1
        
    def eeprom_read(self, idx = 0):
        assert self.eeprom is not None
        try:
            return self.eeprom.read(idx, MAX_EEPROM_BYTES_PER_READ)
        except ValueError as e:
            print(e)
            return -1
        
    def eeprom_wipe(self):
        assert self.eeprom is not None
        self.eeprom.wipe()
        
    # 4G
    def write_to_modem(self, command):
        if self.modem is None:
            return
        return self.modem.write(command)
                              
    def read_from_modem(self):
        if self.modem is None:
            return
        return self.modem.read()
    
#     def disconnect(self):
#         self.modem.power_off()
    
    # Helper functions
#     def read_to_ws(self, ws = None, target = None):
#         if ws is None:
#             return
#         if target == "CAN":
#             self._()
        
        

