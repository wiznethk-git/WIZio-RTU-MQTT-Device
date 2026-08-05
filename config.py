DHCP = True
DEBUG = True

# UART
UART_CONFIGS = {
    "baudrate": 9600,
    "bits": 8,
    "parity": None,
    "stop": 1,
}

# CAN
CAN_USED = True
CAN_CONFIGS = {
    "baudrate": 500000,
    "mode": 0, # 0 for normal
}

# 4G
MODEM_CONFIGS = {
    "baudrate": 115200,
    "bits": 8,
    "parity": None,
    "stop": 1
} 

