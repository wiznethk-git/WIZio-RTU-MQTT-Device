import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText
import paho.mqtt.client as mqtt
import time
import json
import ast
import queue
import base64
import binascii
try:
    from config import USERNAME, PASSWORD
except ImportError:
    print('Cannot import configurations. Use USERNAME and PASSWORD as None instead.')
    USERNAME, PASSWORD = None, None

class DeviceDashboard:
    def __init__(self, root):
        self.root = root
        self.root.title("W55MH32 MQTT Dashboard")
        self.root.geometry("1520x850")
        self.root.configure(bg="#111827")
        self.root.resizable(True, True)

        # -------------------------------------------------
        # MQTT connection configuration
        # Kept from original dashboard.
        # -------------------------------------------------
        self.broker_ip = tk.StringVar(value="127.0.0.1")
        self.broker_port = tk.StringVar(value="1883")
        self.client_id = tk.StringVar(value=f"pc_dashboard_{int(time.time())}")
        self.username = USERNAME
        self.password =  PASSWORD

        # -------------------------------------------------
        # MQTT topic configuration
        # -------------------------------------------------
        # Use separate set/state topics so this dashboard does not receive
        # its own command messages after publishing.
        self.topic_network_config_set = tk.StringVar(value="network_config/set")
        self.topic_network_config_state = tk.StringVar(value="network_config/state")
        self.topic_digital_output_set = tk.StringVar(value="digital_output/{pin}/set")
        self.topic_digital_output_state = tk.StringVar(value="digital_output/{pin}/state")
        self.topic_digital_input_state = tk.StringVar(value="digital_input/{pin}/state")
        self.topic_analog_output_set = tk.StringVar(value="analog_output/{pin}/set")
        self.topic_analog_output_state = tk.StringVar(value="analog_output/{pin}/state")
        self.topic_analog_input_state = tk.StringVar(value="analog_input/state")
        self.topic_eeprom_write = tk.StringVar(value="eeprom/write")
        self.topic_eeprom_wipe = tk.StringVar(value="eeprom/wipe")
        self.topic_eeprom_read_request = tk.StringVar(value="eeprom/read/request")
        self.topic_eeprom_read_response = tk.StringVar(value="eeprom/read/response")
        self.topic_uart_write = tk.StringVar(value="uart/write")
        self.topic_uart_read = tk.StringVar(value="uart/read")
        self.topic_modem_command = tk.StringVar(value="modem/command")
        self.topic_modem_recv = tk.StringVar(value="modem/recv")
        self.topic_can_send = tk.StringVar(value="CAN/send")
        self.topic_can_recv = tk.StringVar(value="CAN/recv")
        self.topic_serial_mode = tk.StringVar(value="serial_mode")

        # -------------------------------------------------
        # Runtime state
        # -------------------------------------------------
        self.connected = False
        self.client = None
        self.closing = False
        self.disconnect_requested = False
        self.gui_queue = queue.Queue()

        self.last_topic = "--"
        self.last_message = "--"

        # Network configuration state
        self.net_ip = tk.StringVar(value="")
        self.net_mask = tk.StringVar(value="")
        self.net_gateway = tk.StringVar(value="")
        self.net_dns = tk.StringVar(value="")
        self.net_mode = tk.StringVar(value="dhcp")  # "static" or "dhcp"

        # Digital I/O state
        self.output_states = {f"RY{i}": False for i in range(1, 9)}
        self.input_states = {f"IN{i}": False for i in range(1, 9)}
        self.output_buttons = {}
        self.input_labels = {}

        # Analog I/O state
        self.analog_input_states = {f"AI{i}": 0.0 for i in range(4)}
        # AI0 and AI1 default to current mode. AI2 and AI3 default to voltage mode.
        # mode 0 = voltage mode, mode 1 = current mode.
        self.analog_input_modes = {
            "AI0": 1,
            "AI1": 1,
            "AI2": 0,
            "AI3": 0,
        }
        self.analog_output_states = {f"AO{i}": 0.0 for i in range(2)}
        self.analog_input_labels = {}
        self.analog_input_mode_labels = {}
        self.analog_output_vars = {}
        self.analog_output_state_labels = {}

        # EEPROM state
        self.eeprom_max_write_bytes = 1000
        self.eeprom_last_read_bytes = b""

        # UART state
        self.uart_last_read_bytes = b""

        # 4G modem state
        self.modem_last_read_bytes = b""

        # CAN / serial interface state
        self.can_last_read = None
        self.serial_mode = None

        # -------------------------------------------------
        # Build UI
        # -------------------------------------------------
        self.setup_styles()
        self.build_header()
        self.build_main_tabs()

        self.create_mqtt_client()
        self.root.after(50, self.process_gui_queue)
        self.root.after(100, self.init_status_widgets)
        self.update_clock()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # =====================================================
    # MQTT
    # =====================================================
    def create_mqtt_client(self):
        self.client = mqtt.Client(
            client_id=self.client_id.get(),
            protocol=mqtt.MQTTv311
        )
        self.client.username_pw_set(
            username=self.username,
            password=self.password,
        )
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect

    def connect_mqtt(self):
        if self.closing:
            return
        if self.connected:
            self.write_log("Already connected to broker")
            return

        try:
            broker = self.broker_ip.get().strip()
            port = int(self.broker_port.get().strip())

            self.create_mqtt_client()

            self.write_log(f"Connecting to broker {broker}:{port}")
            self.client.connect(broker, port, 60)
            self.client.loop_start()

        except Exception as e:
            messagebox.showerror("Connection Error", str(e))
            self.write_log(f"Connection error: {e}")

    def disconnect_mqtt(self):
        if self.closing:
            return

        if self.client is None:
            return

        if not self.connected:
            self.write_log("Already disconnected")
            return

        try:
            self.disconnect_requested = True
            self.write_log("Disconnecting from MQTT broker...")
            self.client.disconnect()
        except Exception as e:
            self.disconnect_requested = False
            self.write_log(f"Disconnect error: {e}")

    def schedule_gui_update(self, callback):
        if self.closing:
            return

        self.gui_queue.put(callback)

    def process_gui_queue(self):
        if self.closing:
            return

        try:
            while True:
                callback = self.gui_queue.get_nowait()
                try:
                    callback()
                except tk.TclError:
                    pass
                except Exception as e:
                    print("GUI callback error:", e)
        except queue.Empty:
            pass

        try:
            self.root.after(50, self.process_gui_queue)
        except tk.TclError:
            pass

    def on_connect(self, client, userdata, flags, rc):
        if self.closing:
            return

        def gui_update():
            if self.closing:
                return
            if rc == 0:
                self.connected = True
                self.update_connection_badge("CONNECTED", "#10B981")
                self.connection_value.config(text="Connected to MQTT broker")
                self.write_log("Connected to MQTT broker successfully")
                self.subscribe_configured_topics()
            else:
                self.connected = False
                self.update_connection_badge(f"FAILED rc={rc}", "#F59E0B")
                self.connection_value.config(text=f"Connect failed rc={rc}")
                self.write_log(f"MQTT connect failed, rc={rc}")

        self.schedule_gui_update(gui_update)

    def on_disconnect(self, client, userdata, rc):
        if self.closing:
            return

        def gui_update():
            if self.closing:
                return

            self.connected = False
            self.update_connection_badge("DISCONNECTED", "#EF4444")
            self.connection_value.config(text="Disconnected")

            if rc == 0:
                self.write_log("Disconnected from MQTT broker")
            else:
                self.write_log(f"Unexpected MQTT disconnection, rc={rc}")

            if self.disconnect_requested:
                self.disconnect_requested = False
                try:
                    client.loop_stop()
                except Exception as e:
                    self.write_log(f"MQTT loop stop error: {e}")

        self.schedule_gui_update(gui_update)

    def subscribe_configured_topics(self):
        if self.closing or not self.connected or self.client is None:
            return

        # Subscribe only to state/feedback topics.
        # Do not subscribe to /set topics, otherwise the dashboard receives
        # its own command payload immediately after publishing.
        topics = [
            self.topic_network_config_state.get().strip(),
            self.topic_pattern_to_subscribe_topic(self.topic_digital_input_state.get().strip()),
            self.topic_pattern_to_subscribe_topic(self.topic_digital_output_state.get().strip()),
            self.topic_analog_input_state.get().strip(),
            self.topic_pattern_to_subscribe_topic(self.topic_analog_output_state.get().strip()),
            self.topic_eeprom_read_response.get().strip(),
            self.topic_uart_read.get().strip(),
            self.topic_modem_recv.get().strip(),
            self.topic_can_recv.get().strip(),
            self.topic_serial_mode.get().strip(),
        ]

        seen = set()
        for topic in topics:
            if topic and topic not in seen:
                self.client.subscribe(topic)
                self.write_log(f"Subscribed to [{topic}]")
                seen.add(topic)

    def apply_topics(self):
        if self.closing:
            return
        if not self.connected:
            messagebox.showinfo("Not Connected", "Topics were saved locally. Connect to MQTT to subscribe.")
            self.write_log("Topic values updated locally")
            return

        self.subscribe_configured_topics()
        messagebox.showinfo("Topics Applied", "Subscribed to the configured topics.")

    def on_message(self, client, userdata, msg):
        if self.closing:
            return

        raw_payload = msg.payload
        decoded_payload = raw_payload.decode("utf-8", errors="replace")
        payload = decoded_payload.strip()
        topic = msg.topic.strip()

        def gui_update():
            if self.closing:
                return

            self.last_topic = topic
            self.last_message = decoded_payload

            if hasattr(self, "last_topic_value"):
                self.last_topic_value.config(text=topic)
            if hasattr(self, "last_message_value"):
                self.last_message_value.config(text=decoded_payload)

            preview = decoded_payload.replace("\n", "\\n")
            if len(preview) > 300:
                preview = preview[:300] + "..."
            self.write_log(f"Received [{topic}] {len(raw_payload)} bytes -> {preview}")

            digital_input_pin = self.match_pin_topic(self.topic_digital_input_state.get().strip(), topic)
            digital_output_pin = self.match_pin_topic(self.topic_digital_output_state.get().strip(), topic)
            analog_output_pin = self.match_pin_topic(self.topic_analog_output_state.get().strip(), topic)

            if topic == self.topic_network_config_state.get().strip():
                self.handle_network_config_payload(payload)
            elif digital_input_pin in self.input_states:
                self.handle_digital_input_payload(payload, pin=digital_input_pin)
            elif digital_output_pin in self.output_states:
                self.handle_digital_output_payload(payload, pin=digital_output_pin)
            elif topic == self.topic_analog_input_state.get().strip():
                self.handle_analog_input_payload(payload)
            elif analog_output_pin in self.analog_output_states:
                self.handle_analog_output_payload(payload, pin=analog_output_pin)
            elif topic == self.topic_analog_output_state.get().strip():
                # Backward-compatible grouped analog output state topic.
                self.handle_analog_output_payload(payload)
            elif topic == self.topic_eeprom_read_response.get().strip():
                self.handle_eeprom_read_response_payload(raw_payload)
            elif topic == self.topic_uart_read.get().strip():
                self.handle_uart_read_payload(raw_payload)
            elif topic == self.topic_modem_recv.get().strip():
                self.handle_modem_recv_payload(raw_payload)
            elif topic == self.topic_can_recv.get().strip():
                self.handle_can_recv_payload(raw_payload)
            elif topic == self.topic_serial_mode.get().strip():
                self.handle_serial_mode_payload(raw_payload)

        self.schedule_gui_update(gui_update)

    def publish_message(self, topic, payload):
        if self.closing:
            return False
        if not self.connected:
            messagebox.showwarning("Not Connected", "Please connect to the MQTT broker first.")
            self.write_log("Publish failed: broker not connected")
            return False

        try:
            self.client.publish(topic, payload)
            if isinstance(payload, (bytes, bytearray)):
                preview = bytes(payload[:120]).decode("utf-8", errors="replace")
                if len(payload) > 120:
                    preview += "..."
                self.write_log(f"Published [{topic}] {len(payload)} bytes -> {preview}")
            else:
                self.write_log(f"Published [{topic}] -> {payload}")
            return True
        except Exception as e:
            self.write_log(f"Publish error: {e}")
            return False

    # =====================================================
    # Payload helpers
    # =====================================================
    def parse_mapping_payload(self, payload):
        """
        Accept normal JSON first:
            {"RY1": true}
        Also accept Python-style dict strings during testing:
            {'RY1': True}
        """
        try:
            obj = json.loads(payload)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

        try:
            obj = ast.literal_eval(payload)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

        self.write_log(f"Invalid mapping payload: {payload}")
        return None

    def bool_from_value(self, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        if isinstance(value, str):
            upper = value.strip().upper()
            if upper in ("1", "TRUE", "ON", "HIGH", "YES"):
                return True
            if upper in ("0", "FALSE", "OFF", "LOW", "NO"):
                return False
        return bool(value)

    def float_from_value(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def int_from_value(self, value, default=0):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def topic_pattern_to_subscribe_topic(self, pattern):
        """
        Dashboard configuration uses {pin} to make per-pin topics readable.
        MQTT subscribe needs + as the single-level wildcard.
        """
        return pattern.replace("{pin}", "+")

    def topic_pattern_with_pin(self, pattern, pin):
        """
        Convert a configured per-pin pattern into a real publish topic.
        Supports either digital_output/{pin}/set or digital_output/+/set.
        """
        return pattern.replace("{pin}", pin).replace("+", pin)

    def match_pin_topic(self, pattern, topic):
        """
        Match topics such as:
            digital_input/{pin}/state
            digital_input/+/state

        Returns the captured pin string when matched, otherwise None.
        """
        pattern = self.topic_pattern_to_subscribe_topic(pattern)
        pattern_parts = pattern.split("/")
        topic_parts = topic.split("/")

        if len(pattern_parts) != len(topic_parts):
            return None

        captured_pin = None
        for pattern_part, topic_part in zip(pattern_parts, topic_parts):
            if pattern_part == "+":
                captured_pin = topic_part.upper()
                continue
            if pattern_part != topic_part:
                return None

        return captured_pin

    # =====================================================
    # Network configuration
    # =====================================================
    def handle_network_config_payload(self, payload):
        data = self.parse_mapping_payload(payload)
        if data is None:
            return

        if "ip" in data:
            self.net_ip.set(str(data.get("ip", "")))
        if "mask" in data:
            self.net_mask.set(str(data.get("mask", "")))
        if "gateway" in data:
            self.net_gateway.set(str(data.get("gateway", "")))
        if "dns" in data:
            self.net_dns.set(str(data.get("dns", "")))
        if "dhcp" in data:
            self.net_mode.set("dhcp" if self.bool_from_value(data.get("dhcp")) else "static")

        self.write_log("Network configuration UI updated from MQTT payload")

    def publish_network_config(self):
        payload_obj = {
            "ip": self.net_ip.get().strip(),
            "mask": self.net_mask.get().strip(),
            "gateway": self.net_gateway.get().strip(),
            "dns": self.net_dns.get().strip(),
            "dhcp": self.net_mode.get() == "dhcp",
        }
        payload = json.dumps(payload_obj)
        self.publish_message(self.topic_network_config_set.get().strip(), payload)

    # =====================================================
    # Digital input/output
    # =====================================================
    def handle_digital_input_payload(self, payload, pin=None):
        data = self.parse_mapping_payload(payload)
        if data is None:
            return

        # Preferred per-pin payload:
        #     topic: digital_input/IN1/state
        #     payload: {"value": 0}
        if pin is not None:
            pin = str(pin).upper()
            if pin in self.input_states and "value" in data:
                self.input_states[pin] = self.bool_from_value(data.get("value"))
                self.update_input_widgets()
            return

        # Backward-compatible grouped payload:
        #     {"IN1": true, "IN2": false}
        for key, value in data.items():
            key = str(key).upper()
            if key in self.input_states:
                self.input_states[key] = self.bool_from_value(value)

        self.update_input_widgets()

    def handle_digital_output_payload(self, payload, pin=None):
        data = self.parse_mapping_payload(payload)
        if data is None:
            return

        # Preferred per-pin payload:
        #     topic: digital_output/RY1/state
        #     payload: {"value": 1}
        if pin is not None:
            pin = str(pin).upper()
            if pin in self.output_states and "value" in data:
                self.output_states[pin] = self.bool_from_value(data.get("value"))
                self.update_output_widgets()
            return

        # Backward-compatible single/grouped payloads:
        #     {"pin": "RY1", "value": true}
        #     {"RY1": true, "RY2": false}
        if "pin" in data and "value" in data:
            output_pin = str(data.get("pin", "")).upper()
            if output_pin in self.output_states:
                self.output_states[output_pin] = self.bool_from_value(data.get("value"))
        else:
            for key, value in data.items():
                key = str(key).upper()
                if key in self.output_states:
                    self.output_states[key] = self.bool_from_value(value)

        self.update_output_widgets()

    def publish_output_state(self, output_name):
        output_name = output_name.upper()
        if output_name not in self.output_states:
            return

        new_state = not self.output_states[output_name]
        payload = json.dumps({
            "value": 1 if new_state else 0,
        })

        topic_pattern = self.topic_digital_output_set.get().strip()
        topic = self.topic_pattern_with_pin(topic_pattern, output_name)

        # Do not update the relay button optimistically here.
        # The button state is updated only when digital_output/<pin>/state arrives.
        self.publish_message(topic, payload)

    # =====================================================
    # Analog input/output
    # =====================================================
    def handle_analog_input_payload(self, payload):
        data = self.parse_mapping_payload(payload)
        if data is None:
            return

        self.update_analog_input_state_from_payload(data)
        self.update_analog_input_widgets()

    def handle_analog_output_payload(self, payload, pin=None):
        data = self.parse_mapping_payload(payload)
        if data is None:
            return

        # Preferred per-pin payload:
        #     topic: analog_out/AO0/state
        #     payload: {"voltage": 1.23}
        if pin is not None:
            pin = str(pin).upper()
            if pin in self.analog_output_states:
                voltage = self.float_from_value(data.get("voltage", data.get("value")))
                if voltage is not None:
                    self.analog_output_states[pin] = voltage
                    self.update_analog_output_widgets()
            return

        # Backward-compatible grouped payload support.
        self.update_analog_state_from_payload(data, self.analog_output_states)
        self.update_analog_output_widgets()

    def update_analog_input_state_from_payload(self, data):
        """
        Preferred analog input payload:
            {"0": {"value": 1.23, "mode": 0}, "1": {"value": 0.012, "mode": 1}}

        The top-level key is the analog input index:
            "0" -> AI0, "1" -> AI1, etc.

        mode:
            0 -> voltage mode, display V
            1 -> current mode, display A

        If mode is missing for a channel, keep the previous mode and only update
        the value. This also keeps compatibility with older payload formats.
        """
        # Single-channel format support:
        #     {"pin": "AI0", "value": 1.23, "mode": 0}
        #     {"pin": "AI0", "voltage": 1.23}
        if "pin" in data:
            pin = str(data.get("pin", "")).upper()
            if pin in self.analog_input_states:
                value = self.float_from_value(data.get("value", data.get("voltage")))
                if value is not None:
                    self.analog_input_states[pin] = value

                if "mode" in data:
                    mode = self.int_mode_from_value(data.get("mode"))
                    if mode is not None:
                        self.analog_input_modes[pin] = mode
            return

        for key, item in data.items():
            pin = self.analog_input_pin_from_key(key)
            if pin not in self.analog_input_states:
                continue

            if isinstance(item, dict):
                value = self.float_from_value(item.get("value", item.get("voltage")))
                if value is not None:
                    self.analog_input_states[pin] = value

                # Only change the visual mode if mode is provided.
                if "mode" in item:
                    mode = self.int_mode_from_value(item.get("mode"))
                    if mode is not None:
                        self.analog_input_modes[pin] = mode
            else:
                # Older grouped payload support:
                #     {"AI0": 1.23, "AI1": 2.34}
                value = self.float_from_value(item)
                if value is not None:
                    self.analog_input_states[pin] = value

    def analog_input_pin_from_key(self, key):
        key = str(key).upper().strip()

        if key.startswith("AI"):
            return key

        try:
            return "AI{}".format(int(key))
        except ValueError:
            return key

    def int_mode_from_value(self, value):
        try:
            mode = int(value)
        except (TypeError, ValueError):
            return None

        if mode in (0, 1):
            return mode
        return None

    def update_analog_state_from_payload(self, data, states):
        # Accept single state payloads:
        #     {"pin": "AO0", "voltage": 1.23}
        #     {"pin": "AO0", "value": 1.23}
        # Also accept grouped state payloads:
        #     {"AO0": 1.23, "AO1": 2.34}
        #     {"AO0": {"voltage": 1.23}, "AO1": {"voltage": 2.34}}
        if "pin" in data:
            pin = str(data.get("pin", "")).upper()
            voltage = self.float_from_value(data.get("voltage", data.get("value")))
            if pin in states and voltage is not None:
                states[pin] = voltage
            return

        for key, value in data.items():
            pin = str(key).upper()
            if isinstance(value, dict):
                voltage = self.float_from_value(value.get("voltage", value.get("value")))
            else:
                voltage = self.float_from_value(value)
            if pin in states and voltage is not None:
                states[pin] = voltage

    def publish_analog_output_state(self, output_name):
        output_name = output_name.upper()
        if output_name not in self.analog_output_states:
            return

        voltage = self.float_from_value(
            self.analog_output_vars[output_name].get().strip()
        )
        if voltage is None:
            messagebox.showwarning(
                "Invalid Voltage",
                f"Enter a numeric voltage for {output_name}."
            )
            return

        # Limit the requested output to the supported 0–10 V range.
        voltage = max(0.0, min(10.0, voltage))

        payload = json.dumps({
            "voltage": voltage,
        })

        topic_pattern = self.topic_analog_output_set.get().strip()
        topic = self.topic_pattern_with_pin(topic_pattern, output_name)

        if self.publish_message(topic, payload):
            # Immediately show the value that was actually published.
            # For example, entering 15 changes the text box to 10.000.
            self.analog_output_vars[output_name].set(f"{voltage:.3f}")

    # =====================================================
    # EEPROM
    # =====================================================
    def update_eeprom_write_count(self, _event=None):
        if not hasattr(self, "eeprom_write_box"):
            return

        text = self.eeprom_write_box.get("1.0", "end-1c")
        byte_count = len(text.encode("utf-8"))
        color = "#F87171" if byte_count > self.eeprom_max_write_bytes else "#9CA3AF"
        self.eeprom_write_count_label.config(
            text=f"{byte_count}/{self.eeprom_max_write_bytes} bytes UTF-8",
            fg=color,
        )

    def publish_eeprom_write(self):
        if not hasattr(self, "eeprom_write_box"):
            return

        text = self.eeprom_write_box.get("1.0", "end-1c")
        data = text.encode("utf-8")

        if len(data) > self.eeprom_max_write_bytes:
            messagebox.showwarning(
                "EEPROM Write Too Large",
                f"EEPROM write allows only {self.eeprom_max_write_bytes} bytes each time. Current payload is {len(data)} bytes.",
            )
            self.write_log(f"EEPROM write blocked: {len(data)} bytes exceeds limit")
            return

        topic = self.topic_eeprom_write.get().strip()
        if self.publish_message(topic, data):
            self.write_log(f"EEPROM write request sent to [{topic}] with {len(data)} bytes")

    def publish_eeprom_wipe(self):
        topic = self.topic_eeprom_wipe.get().strip()
        if not topic:
            messagebox.showwarning("Missing Topic", "EEPROM wipe topic is empty.")
            return

        if not messagebox.askyesno("Confirm EEPROM Wipe", "Publish True to eeprom/wipe and wipe EEPROM on the device?"):
            return

        if self.publish_message(topic, "True"):
            self.write_log(f"EEPROM wipe request sent to [{topic}] -> True")

    def request_eeprom_read(self):
        # MQTT does not have HTTP-style fetch. This publishes a request message.
        # The device should subscribe to eeprom/read/request, read EEPROM at the
        # requested index, then publish the result to eeprom/read/response.
        try:
            index = int(self.eeprom_read_index_var.get().strip())
        except ValueError:
            messagebox.showwarning("Invalid EEPROM Index", "Enter an integer EEPROM read index.")
            return

        payload = json.dumps({"index": index})
        topic = self.topic_eeprom_read_request.get().strip()
        if self.publish_message(topic, payload):
            self.write_log(f"EEPROM read request sent to [{topic}] -> {payload}")

    def handle_eeprom_read_response_payload(self, raw_payload):
        self.eeprom_last_read_bytes = bytes(raw_payload)
        decoded = raw_payload.decode("utf-8", errors="replace")

        # Keep the returned string visible even if the EEPROM contains odd bytes.
        # Invalid UTF-8 bytes are shown with replacement characters, not discarded.
        if hasattr(self, "eeprom_read_box"):
            self.eeprom_read_box.config(state="normal")
            self.eeprom_read_box.delete("1.0", "end")
            self.eeprom_read_box.insert("1.0", decoded)
            self.eeprom_read_box.config(state="disabled")

        if hasattr(self, "eeprom_read_info_label"):
            self.eeprom_read_info_label.config(text=f"Last response: {len(raw_payload)} bytes decoded as UTF-8")

        self.write_log(f"EEPROM read response displayed: {len(raw_payload)} bytes")

    # =====================================================
    # UART
    # =====================================================
    def publish_uart_write(self):
        if not hasattr(self, "uart_write_box"):
            return

        text = self.uart_write_box.get("1.0", "end-1c")
        data = text.encode("utf-8")
        topic = self.topic_uart_write.get().strip()

        if not topic:
            messagebox.showwarning("Missing Topic", "UART write topic is empty.")
            return

        if self.publish_message(topic, data):
            self.append_uart_message("SEND", text, len(data))
            self.write_log(f"UART write sent to [{topic}] with {len(data)} bytes")

    def handle_uart_read_payload(self, raw_payload):
        self.uart_last_read_bytes = bytes(raw_payload)
        decoded = raw_payload.decode("utf-8", errors="replace")
        self.append_uart_message("RECEIVE", decoded, len(raw_payload))
        self.write_log(f"UART read displayed from device: {len(raw_payload)} bytes")

    def append_uart_message(self, direction, text, byte_count):
        if not hasattr(self, "uart_history_box"):
            return

        timestamp = time.strftime("%H:%M:%S")
        self.uart_history_box.config(state="normal")

        if direction == "SEND":
            header = f"[{timestamp}] SEND PC -> DEVICE ({byte_count} bytes)\n"
            tag = "send"
        else:
            header = f"[{timestamp}] RECEIVE DEVICE UART -> PC ({byte_count} bytes)\n"
            tag = "receive"

        self.uart_history_box.insert("end", header, tag)
        self.uart_history_box.insert("end", text + "\n")
        self.uart_history_box.insert("end", "-" * 72 + "\n")
        self.uart_history_box.config(state="disabled")
        self.uart_history_box.see("end")

    def clear_uart_history(self):
        if hasattr(self, "uart_history_box"):
            self.uart_history_box.config(state="normal")
            self.uart_history_box.delete("1.0", "end")
            self.uart_history_box.config(state="disabled")

    def clear_uart_write_box(self):
        if hasattr(self, "uart_write_box"):
            self.uart_write_box.delete("1.0", "end")


    # =====================================================
    # 4G modem
    # =====================================================
    def publish_modem_command(self):
        """
        Publish the text exactly as entered.

        The MQTT payload is raw UTF-8 text, not JSON and not Base64.
        Actual newline characters entered in the text area are preserved.
        """
        if not hasattr(self, "modem_command_box"):
            return

        text = self.modem_command_box.get("1.0", "end-1c")
        data = text.encode("utf-8")
        topic = self.topic_modem_command.get().strip()

        if not topic:
            messagebox.showwarning("Missing Topic", "Modem command topic is empty.")
            return

        if self.publish_message(topic, data):
            self.append_modem_message("SEND", text, len(data))
            self.write_log(
                f"Modem command sent to [{topic}] with {len(data)} UTF-8 bytes"
            )

    def handle_modem_recv_payload(self, raw_payload):
        """
        Expected MQTT payload on modem/recv:

            publish("modem/recv", {
                "msg": b2a_base64(buf).strip().decode("utf-8"),
                "work_mode": 0,
            })

        work_mode 0 displays a normal labelled RX block. work_mode > 0 appends
        decoded text directly so terminal streaming output stays continuous.
        """
        try:
            payload_obj = json.loads(raw_payload.decode("utf-8", errors="replace"))
        except (TypeError, ValueError) as exc:
            self.write_log(
                f"Invalid modem JSON payload received from "
                f"[{self.topic_modem_recv.get().strip()}]: {exc}"
            )
            return

        if not isinstance(payload_obj, dict):
            self.write_log(
                f"Invalid modem payload received from "
                f"[{self.topic_modem_recv.get().strip()}]: expected JSON object"
            )
            return

        encoded_payload = str(payload_obj.get("msg", "")).strip().encode("ascii", errors="ignore")
        work_mode = self.int_from_value(payload_obj.get("work_mode"), default=0)

        try:
            decoded_bytes = base64.b64decode(encoded_payload, validate=True)
        except (ValueError, binascii.Error) as exc:
            self.write_log(
                f"Invalid Base64 payload received from "
                f"[{self.topic_modem_recv.get().strip()}]: {exc}"
            )
            return

        self.modem_last_read_bytes = decoded_bytes
        decoded_text = decoded_bytes.decode("utf-8", errors="replace")

        self.append_modem_message(
            "RECEIVE",
            decoded_text + '\n',
            len(decoded_bytes),
            encoded_byte_count=len(raw_payload),
            stream_mode=work_mode > 0,
        )

        if hasattr(self, "modem_last_received_label"):
            self.modem_last_received_label.config(
                text=(
                    f"Last RX: {len(decoded_bytes)} decoded bytes "
                    f"from {len(raw_payload)} MQTT bytes, work_mode={work_mode}"
                )
            )

        self.write_log(
            f"Modem receive decoded from [{self.topic_modem_recv.get().strip()}]: "
            f"{len(raw_payload)} MQTT bytes -> {len(decoded_bytes)} raw bytes, "
            f"work_mode={work_mode}"
        )

    def append_modem_message(
        self,
        direction,
        text,
        byte_count,
        encoded_byte_count=None,
        stream_mode=False,
    ):
        if not hasattr(self, "modem_history_box"):
            return

        timestamp = time.strftime("%H:%M:%S")
        self.modem_history_box.config(state="normal")

        if stream_mode:
            tag = "stream_receive" if direction != "SEND" else "stream_send"
            self.modem_history_box.insert("end", text, tag)
            self.modem_history_box.config(state="disabled")
            self.modem_history_box.see("end")
            return

        if direction == "SEND":
            header = (
                f"[{timestamp}] SEND DASHBOARD -> 4G MODULE "
                f"({byte_count} UTF-8 bytes)\n"
            )
            tag = "send"
        else:
            if encoded_byte_count is None:
                encoded_byte_count = 0
            header = (
                f"[{timestamp}] RECEIVE 4G MODULE -> DASHBOARD "
                f"({byte_count} decoded bytes, "
                f"{encoded_byte_count} Base64 MQTT bytes)\n"
            )
            tag = "receive"

        self.modem_history_box.insert("end", header, tag)
        self.modem_history_box.insert("end", text)

        # Keep consecutive messages visually separated without changing
        # the decoded modem data itself.
        if not text.endswith("\n"):
            self.modem_history_box.insert("end", "\n")

        self.modem_history_box.insert("end", "-" * 78 + "\n")
        self.modem_history_box.config(state="disabled")
        self.modem_history_box.see("end")

    def clear_modem_history(self):
        if hasattr(self, "modem_history_box"):
            self.modem_history_box.config(state="normal")
            self.modem_history_box.delete("1.0", "end")
            self.modem_history_box.config(state="disabled")

        if hasattr(self, "modem_last_received_label"):
            self.modem_last_received_label.config(text="Last RX: --")

    def clear_modem_command_box(self):
        if hasattr(self, "modem_command_box"):
            self.modem_command_box.delete("1.0", "end")

    # =====================================================
    # Serial interface mode
    # =====================================================
    def handle_serial_mode_payload(self, raw_payload):
        """
        Expected payloads:
            {"Mode": "CAN"}
            {"Mode": "RS485"}
        """
        try:
            if isinstance(raw_payload, (bytes, bytearray)):
                payload_text = bytes(raw_payload).decode("utf-8")
            else:
                payload_text = str(raw_payload)

            data = json.loads(payload_text)
        except (ValueError, UnicodeError) as exc:
            self.write_log(f"Invalid serial_mode JSON payload: {exc}")
            return

        if not isinstance(data, dict):
            self.write_log("Invalid serial_mode payload: expected a mapping")
            return

        mode = data.get("Mode", data.get("mode"))
        if not isinstance(mode, str):
            self.write_log("Invalid serial_mode payload: Mode must be a string")
            return

        mode = mode.strip().upper()
        if mode not in ("CAN", "RS485"):
            self.write_log(f"Unsupported serial mode: {mode!r}")
            return

        self.apply_serial_mode(mode)

    def apply_serial_mode(self, mode):
        if not hasattr(self, "notebook"):
            return

        self.serial_mode = mode
        selected_tab = self.notebook.select()

        if mode == "CAN":
            self.notebook.tab(self.tab_can, state="normal")
            self.notebook.tab(self.tab_uart, state="disabled")

            if selected_tab == str(self.tab_uart):
                self.notebook.select(self.tab_can)

            self.write_log("Serial mode changed to CAN: CAN tab enabled, Serial tab disabled")
        else:
            self.notebook.tab(self.tab_uart, state="normal")
            self.notebook.tab(self.tab_can, state="disabled")

            if selected_tab == str(self.tab_can):
                self.notebook.select(self.tab_uart)

            self.write_log("Serial mode changed to RS485: Serial tab enabled, CAN tab disabled")

    # =====================================================
    # CAN
    # =====================================================
    def parse_can_payload(self, raw_payload):
        """
        Expected CAN receive payload:
            {"frame_type": "standard", "id": "0x0", "data": "1c98ccba0000"}
        """
        try:
            if isinstance(raw_payload, (bytes, bytearray)):
                payload_text = bytes(raw_payload).decode("utf-8")
            else:
                payload_text = str(raw_payload)

            payload = json.loads(payload_text)
        except (ValueError, UnicodeError) as exc:
            self.write_log(f"Invalid CAN JSON payload: {exc}")
            return None

        if not isinstance(payload, dict):
            self.write_log("Invalid CAN payload: expected a mapping")
            return None

        frame_type = payload.get("frame_type")
        raw_can_id = payload.get("id")
        data_hex = payload.get("data")

        if not isinstance(frame_type, str):
            self.write_log("Invalid CAN payload: frame_type must be a string")
            return None

        frame_type = frame_type.strip().lower()
        if frame_type not in ("standard", "extended"):
            self.write_log("Invalid CAN payload: frame_type must be standard or extended")
            return None

        try:
            if isinstance(raw_can_id, str):
                can_id = int(raw_can_id.strip(), 0)
            else:
                can_id = int(raw_can_id)
        except (TypeError, ValueError):
            self.write_log("Invalid CAN payload: id must be an integer or 0x-prefixed string")
            return None

        max_can_id = 0x7FF if frame_type == "standard" else 0x1FFFFFFF
        if can_id < 0 or can_id > max_can_id:
            self.write_log(
                f"Invalid {frame_type} CAN ID: expected 0x0 to {hex(max_can_id)}"
            )
            return None

        if not isinstance(data_hex, str):
            self.write_log("Invalid CAN payload: data must be a hexadecimal string")
            return None

        data_hex = data_hex.strip().replace(" ", "").replace(",", "")
        if data_hex.lower().startswith("0x"):
            data_hex = data_hex[2:]

        if len(data_hex) % 2:
            self.write_log("Invalid CAN payload: data must contain complete byte pairs")
            return None

        try:
            can_data = bytes.fromhex(data_hex)
        except ValueError:
            self.write_log("Invalid CAN payload: data contains non-hexadecimal characters")
            return None

        if len(can_data) > 8:
            self.write_log("Invalid CAN payload: classic CAN supports at most 8 data bytes")
            return None

        return {
            "frame_type": frame_type,
            "id": can_id,
            "data": can_data,
        }

    def can_bytes_from_text(self, text):
        """
        Accept either spaced hex bytes:
            01 02 A0 FF

        or compact hexadecimal:
            0102A0FF
        """
        cleaned = text.strip().replace(",", " ").replace("0x", "")
        cleaned = "".join(cleaned.split())

        if not cleaned:
            return b""

        if len(cleaned) % 2:
            raise ValueError("CAN data must contain complete hexadecimal byte pairs")

        try:
            data = bytes.fromhex(cleaned)
        except ValueError:
            raise ValueError("CAN data must contain only hexadecimal bytes")

        if len(data) > 8:
            raise ValueError("Classic CAN allows at most 8 data bytes")

        return data

    def publish_can_message(self):
        can_id_text = self.can_id_var.get().strip()
        can_data_text = self.can_data_box.get("1.0", "end-1c")

        try:
            can_id = int(can_id_text, 0)
        except ValueError:
            messagebox.showwarning("Invalid CAN ID", "Enter a CAN ID such as 0x123 or 291.")
            return

        if can_id < 0 or can_id > 0x1FFFFFFF:
            messagebox.showwarning("Invalid CAN ID", "CAN ID must be between 0x0 and 0x1FFFFFFF.")
            return

        try:
            can_data = self.can_bytes_from_text(can_data_text)
        except ValueError as exc:
            messagebox.showwarning("Invalid CAN Data", str(exc))
            return

        # JSON has no hexadecimal number type, so send the CAN ID as a string.
        # MQTT payload example:
        #     {"id": "0x123", "msg": "0x112233"}
        payload = json.dumps({
            "id": hex(can_id),
            "msg": "0x" + can_data.hex(),
        })
        topic = self.topic_can_send.get().strip()

        if not topic:
            messagebox.showwarning("Missing Topic", "CAN send topic is empty.")
            return

        if self.publish_message(topic, payload):
            self.append_can_message("SEND", can_id, can_data)
            self.write_log(
                f"CAN frame published to [{topic}]: id={hex(can_id)}, "
                f"{len(can_data)} bytes"
            )

    def handle_can_recv_payload(self, raw_payload):
        frame = self.parse_can_payload(raw_payload)
        if frame is None:
            return

        self.can_last_read = frame
        self.append_can_message(
            "RECEIVE",
            frame["id"],
            frame["data"],
            frame_type=frame["frame_type"],
        )

        if hasattr(self, "can_last_received_label"):
            self.can_last_received_label.config(
                text=(
                    f"Last RX: {frame['frame_type'].upper()}  "
                    f"ID={hex(frame['id'])}  DATA={frame['data'].hex()}"
                )
            )

        self.write_log(
            f"CAN frame received from [{self.topic_can_recv.get().strip()}]: "
            f"type={frame['frame_type']}, id={hex(frame['id'])}, "
            f"data={frame['data'].hex()}"
        )

    def append_can_message(self, direction, can_id, message, frame_type=None):
        if not hasattr(self, "can_history_box"):
            return

        timestamp = time.strftime("%H:%M:%S")
        message_hex = " ".join(f"{byte:02X}" for byte in message)
        message_repr = repr(message)

        if frame_type is None:
            frame_type = "standard" if can_id <= 0x7FF else "extended"

        if direction == "SEND":
            header = (
                f"[{timestamp}] SEND DASHBOARD -> DEVICE CAN BUS "
                f"(TYPE={frame_type.upper()}, ID={hex(can_id)}, DLC={len(message)})\n"
            )
            tag = "send"
        else:
            header = (
                f"[{timestamp}] RECEIVE DEVICE CAN BUS -> DASHBOARD "
                f"(TYPE={frame_type.upper()}, ID={hex(can_id)}, DLC={len(message)})\n"
            )
            tag = "receive"

        self.can_history_box.config(state="normal")
        self.can_history_box.insert("end", header, tag)
        self.can_history_box.insert("end", f"HEX: {message_hex or '(empty)'}\n")
        self.can_history_box.insert("end", f"BYTES: {message_repr}\n")
        self.can_history_box.insert("end", "-" * 78 + "\n")
        self.can_history_box.config(state="disabled")
        self.can_history_box.see("end")

    def clear_can_history(self):
        if hasattr(self, "can_history_box"):
            self.can_history_box.config(state="normal")
            self.can_history_box.delete("1.0", "end")
            self.can_history_box.config(state="disabled")

    def clear_can_data_box(self):
        if hasattr(self, "can_data_box"):
            self.can_data_box.delete("1.0", "end")

    # =====================================================
    # UI setup
    # =====================================================
    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("TNotebook", background="#111827", borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background="#374151",
            foreground="white",
            padding=(18, 10),
            font=("Arial", 11, "bold")
        )
        style.map(
            "TNotebook.Tab",
            background=[
                ("disabled", "#171C26"),
                ("selected", "#1F2937"),
                ("!selected", "#374151"),
            ],
            foreground=[
                ("disabled", "#6B7280"),
                ("selected", "#93C5FD"),
                ("!selected", "white"),
            ],
        )

    def build_header(self):
        header = tk.Frame(self.root, bg="#0F172A", height=72)
        header.pack(fill="x")
        header.pack_propagate(False)

        title = tk.Label(
            header,
            text="W55MH32 MQTT DEVICE DASHBOARD",
            bg="#0F172A",
            fg="#F9FAFB",
            font=("Arial", 22, "bold")
        )
        title.pack(side="left", padx=20)

        self.clock_label = tk.Label(
            header,
            text="--:--:--",
            bg="#0F172A",
            fg="#93C5FD",
            font=("Consolas", 16, "bold")
        )
        self.clock_label.pack(side="right", padx=20)

    def build_main_tabs(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=14, pady=14)

        self.tab_connection = tk.Frame(self.notebook, bg="#111827")
        self.tab_network = tk.Frame(self.notebook, bg="#111827")
        self.tab_io = tk.Frame(self.notebook, bg="#111827")
        self.tab_analog_io = tk.Frame(self.notebook, bg="#111827")
        self.tab_eeprom = tk.Frame(self.notebook, bg="#111827")
        self.tab_uart = tk.Frame(self.notebook, bg="#111827")
        self.tab_modem = tk.Frame(self.notebook, bg="#111827")
        self.tab_can = tk.Frame(self.notebook, bg="#111827")

        self.notebook.add(self.tab_connection, text="Connection")
        self.notebook.add(self.tab_network, text="Network Configurations")
        self.notebook.add(self.tab_io, text="Digital I/O")
        self.notebook.add(self.tab_analog_io, text="Analog I/O")
        self.notebook.add(self.tab_eeprom, text="EEPROM")
        self.notebook.add(self.tab_uart, text="Serial")
        self.notebook.add(self.tab_modem, text="4G Modem")
        self.notebook.add(self.tab_can, text="CAN")

        self.build_connection_tab()
        self.build_network_tab()
        self.build_io_tab()
        self.build_analog_io_tab()
        self.build_eeprom_tab()
        self.build_uart_tab()
        self.build_modem_tab()
        self.build_can_tab()

    # =====================================================
    # Tab: Connection
    # =====================================================
    def build_connection_tab(self):
        wrapper = tk.Frame(self.tab_connection, bg="#111827")
        wrapper.pack(fill="both", expand=True, padx=10, pady=10)

        # Three-column layout:
        #   1. broker settings + connection state
        #   2. scrollable topic configuration
        #   3. recent MQTT activity + logs
        left = tk.Frame(wrapper, bg="#111827", width=380)
        left.pack(side="left", fill="y", padx=(0, 14))
        left.pack_propagate(False)

        middle = tk.Frame(wrapper, bg="#111827", width=500)
        middle.pack(side="left", fill="y", padx=(0, 14))
        middle.pack_propagate(False)

        right = tk.Frame(wrapper, bg="#111827")
        right.pack(side="left", fill="both", expand=True)

        # -------------------------------------------------
        # Column 1: old settings and state
        # -------------------------------------------------
        conn_card = self.make_card(left, "BROKER SETTINGS", 360, 280)
        conn_card.pack(pady=(0, 12))

        self.make_label(conn_card, "Broker IP", 24, 60)
        self.make_entry(conn_card, self.broker_ip, 140, 58, 180)

        self.make_label(conn_card, "Port", 24, 100)
        self.make_entry(conn_card, self.broker_port, 140, 98, 180)

        self.make_label(conn_card, "Client ID", 24, 140)
        self.make_entry(conn_card, self.client_id, 140, 138, 180)

        connect_btn = tk.Button(
            conn_card,
            text="CONNECT",
            command=self.connect_mqtt,
            bg="#10B981",
            fg="white",
            relief="flat",
            font=("Arial", 12, "bold"),
            width=12,
            height=1
        )
        connect_btn.place(x=24, y=200)

        disconnect_btn = tk.Button(
            conn_card,
            text="DISCONNECT",
            command=self.disconnect_mqtt,
            bg="#EF4444",
            fg="white",
            relief="flat",
            font=("Arial", 12, "bold"),
            width=12,
            height=1
        )
        disconnect_btn.place(x=190, y=200)

        status_card = self.make_card(left, "CONNECTION STATUS", 360, 310)
        status_card.pack(pady=(0, 12))

        tk.Label(
            status_card,
            text="Broker State",
            bg="#1F2937",
            fg="#9CA3AF",
            font=("Arial", 11)
        ).place(x=24, y=62)

        self.connection_badge = tk.Label(
            status_card,
            text="DISCONNECTED",
            bg="#EF4444",
            fg="white",
            font=("Arial", 12, "bold"),
            padx=14,
            pady=7
        )
        self.connection_badge.place(x=24, y=88)

        self.connection_value = tk.Label(
            status_card,
            text="Disconnected",
            bg="#111827",
            fg="#E5E7EB",
            font=("Consolas", 12),
            anchor="w",
            padx=10
        )
        self.connection_value.place(x=24, y=128, width=300, height=28)

        topic_hint = (
            "Topic Rule:\n"
            "  Command topics use /set\n"
            "  Feedback topics use /state or /read\n\n"
            "The dashboard subscribes only to feedback topics,\n"
            "so it does not receive its own commands."
        )
        tk.Label(
            status_card,
            text=topic_hint,
            bg="#111827",
            fg="#D1D5DB",
            font=("Consolas", 9),
            justify="left",
            anchor="nw",
            padx=10,
            pady=8
        ).place(x=24, y=164, width=310, height=120)

        # -------------------------------------------------
        # Column 2: scrollable topics
        # -------------------------------------------------
        topic_card = self.make_card(middle, "TOPIC CONFIGURATION", 500, 620)
        topic_card.pack(fill="y")

        topic_rows = [
            ("Network Config Set", self.topic_network_config_set),
            ("Network Config State", self.topic_network_config_state),
            ("Digital Output Set", self.topic_digital_output_set),
            ("Digital Output State", self.topic_digital_output_state),
            ("Digital Input State", self.topic_digital_input_state),
            ("Analog Output Set", self.topic_analog_output_set),
            ("Analog Output State", self.topic_analog_output_state),
            ("Analog Input State", self.topic_analog_input_state),
            ("EEPROM Write", self.topic_eeprom_write),
            ("EEPROM Wipe", self.topic_eeprom_wipe),
            ("EEPROM Read Request", self.topic_eeprom_read_request),
            ("EEPROM Read Response", self.topic_eeprom_read_response),
            ("UART Write", self.topic_uart_write),
            ("UART Read", self.topic_uart_read),
            ("Modem Command", self.topic_modem_command),
            ("Modem Receive", self.topic_modem_recv),
            ("CAN Send", self.topic_can_send),
            ("CAN Receive", self.topic_can_recv),
            ("Serial Mode", self.topic_serial_mode),
        ]

        canvas = tk.Canvas(
            topic_card,
            bg="#1F2937",
            highlightthickness=0,
            bd=0
        )
        scrollbar = ttk.Scrollbar(topic_card, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg="#1F2937")
        scroll_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")

        def _sync_scroll_region(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _resize_scroll_frame(event):
            canvas.itemconfig(scroll_window, width=event.width)

        scroll_frame.bind("<Configure>", _sync_scroll_region)
        canvas.bind("<Configure>", _resize_scroll_frame)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.place(x=18, y=55, width=444, height=438)
        scrollbar.place(x=462, y=55, height=438)

        scroll_frame.columnconfigure(0, weight=1)
        for row_idx, (label_text, variable) in enumerate(topic_rows):
            label = tk.Label(
                scroll_frame,
                text=label_text,
                bg="#1F2937",
                fg="#D1D5DB",
                font=("Arial", 11),
                anchor="w"
            )
            label.grid(row=row_idx * 2, column=0, sticky="ew", padx=(10, 16), pady=(8, 2))

            entry = tk.Entry(
                scroll_frame,
                textvariable=variable,
                bg="#111827",
                fg="#F9FAFB",
                insertbackground="white",
                relief="flat",
                font=("Consolas", 11)
            )
            entry.grid(row=row_idx * 2 + 1, column=0, sticky="ew", padx=(10, 16), pady=(0, 14), ipady=6)

        note = tk.Label(
            topic_card,
            text="Edit topic names here, then click Apply. Use {pin} for per-pin topics such as AO0/RY1.",
            bg="#1F2937",
            fg="#9CA3AF",
            font=("Arial", 10),
            justify="left",
            wraplength=430
        )
        note.place(x=28, y=510)

        apply_btn = tk.Button(
            topic_card,
            text="APPLY / SUBSCRIBE STATE TOPICS",
            command=self.apply_topics,
            bg="#2563EB",
            fg="white",
            relief="flat",
            font=("Arial", 11, "bold"),
            width=31,
            height=1
        )
        apply_btn.place(x=90, y=565)

        # -------------------------------------------------
        # Column 3: recent activity and logs
        # -------------------------------------------------
        activity_card = self.make_card(right, "RECENT MQTT ACTIVITY / LOGS", 580, 620)
        activity_card.pack(fill="both", expand=True)

        tk.Label(
            activity_card,
            text="Last Topic",
            bg="#1F2937",
            fg="#9CA3AF",
            font=("Arial", 11)
        ).place(x=28, y=58)

        self.last_topic_value = tk.Label(
            activity_card,
            text="--",
            bg="#111827",
            fg="#E5E7EB",
            font=("Consolas", 10),
            anchor="nw",
            justify="left",
            padx=10,
            pady=6,
            wraplength=390
        )
        self.last_topic_value.place(x=150, y=54, width=400, height=48)

        tk.Label(
            activity_card,
            text="Last Message",
            bg="#1F2937",
            fg="#9CA3AF",
            font=("Arial", 11)
        ).place(x=28, y=122)

        self.last_message_value = tk.Label(
            activity_card,
            text="--",
            bg="#111827",
            fg="#93C5FD",
            font=("Consolas", 10, "bold"),
            anchor="nw",
            justify="left",
            padx=10,
            pady=6,
            wraplength=390
        )
        self.last_message_value.place(x=150, y=118, width=400, height=95)

        self.log_box = ScrolledText(
            activity_card,
            bg="#0B1220",
            fg="#D1D5DB",
            insertbackground="white",
            font=("Consolas", 10),
            relief="flat",
            wrap="word"
        )
        self.log_box.place(x=24, y=240, width=528, height=342)

        clear_btn = tk.Button(
            activity_card,
            text="CLEAR LOG",
            command=lambda: self.log_box.delete("1.0", "end"),
            bg="#374151",
            fg="white",
            relief="flat",
            font=("Arial", 10, "bold"),
            width=12
        )
        clear_btn.place(x=438, y=590)

    # =====================================================
    # Tab: Network Configurations
    # =====================================================
    def build_network_tab(self):
        wrapper = tk.Frame(self.tab_network, bg="#111827")
        wrapper.pack(fill="both", expand=True, padx=10, pady=10)

        config_card = self.make_card(wrapper, "NETWORK CONFIGURATION", 720, 440)
        config_card.pack(side="left", fill="y", padx=(0, 14))

        rows = [
            ("IP Address", self.net_ip),
            ("Subnet Mask", self.net_mask),
            ("Default Gateway", self.net_gateway),
            ("DNS", self.net_dns),
        ]

        self.net_config_entries = []

        y = 78
        for label, var in rows:
            self.make_label(config_card, label, 40, y)
            entry = self.make_entry(config_card, var, 230, y - 4, 390)
            self.net_config_entries.append(entry)
            y += 64

        mode_label = tk.Label(
            config_card,
            text="Address Mode",
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Arial", 11)
        )
        mode_label.place(x=40, y=330)

        static_radio = tk.Radiobutton(
            config_card,
            text="Static",
            variable=self.net_mode,
            value="static",
            bg="#1F2937",
            fg="#F9FAFB",
            selectcolor="#111827",
            activebackground="#1F2937",
            activeforeground="#93C5FD",
            font=("Arial", 11, "bold")
        )
        static_radio.place(x=230, y=326)

        dhcp_radio = tk.Radiobutton(
            config_card,
            text="DHCP",
            variable=self.net_mode,
            value="dhcp",
            bg="#1F2937",
            fg="#F9FAFB",
            selectcolor="#111827",
            activebackground="#1F2937",
            activeforeground="#93C5FD",
            font=("Arial", 11, "bold")
        )
        dhcp_radio.place(x=340, y=326)

        self.net_mode.trace_add("write", self.update_network_mode_widgets)
        self.update_network_mode_widgets()

        publish_btn = tk.Button(
            config_card,
            text="PUBLISH NETWORK CONFIG",
            command=self.publish_network_config,
            bg="#10B981",
            fg="white",
            relief="flat",
            font=("Arial", 12, "bold"),
            width=26,
            height=2
        )
        publish_btn.place(x=230, y=374)

        info_card = self.make_card(wrapper, "NETWORK TOPIC FORMAT", 400, 440)
        info_card.pack(side="left", fill="both", expand=True)

        info = (
            "Set Topic:\n"
            "  network_config/set\n\n"
            "State Topic:\n"
            "  network_config/state\n\n"
            "Payload example:\n"
            "  {\n"
            "    \"ip\": \"10.0.1.146\",\n"
            "    \"mask\": \"255.255.255.0\",\n"
            "    \"gateway\": \"10.0.1.1\",\n"
            "    \"dns\": \"8.8.8.8\",\n"
            "    \"dhcp\": false\n"
            "  }\n\n"
            "Incoming MQTT payload on the network/state\n"
            "topic will update these text boxes\n"
            "and select Static/DHCP automatically."
        )

        tk.Label(
            info_card,
            text=info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 11),
            justify="left",
            anchor="nw"
        ).place(x=28, y=65, width=345, height=340)

    # =====================================================
    # Tab: Input
    # =====================================================
    def build_io_tab(self):
        wrapper = tk.Frame(self.tab_io, bg="#111827")
        wrapper.pack(fill="both", expand=True, padx=10, pady=10)

        output_card = self.make_card(wrapper, "OUTPUT - RY1 TO RY8", 540, 620)
        output_card.pack(side="left", fill="y", padx=(0, 14))

        input_card = self.make_card(wrapper, "INPUT - IN1 TO IN8", 560, 620)
        input_card.pack(side="left", fill="both", expand=True)

        self.build_output_section(output_card)
        self.build_input_section(input_card)

    def build_output_section(self, parent):
        info = (
            "Set Topic:\n"
            "  digital_output/RYx/set\n\n"
            "State Topic:\n"
            "  digital_output/RYx/state\n\n"
            "Payload example:\n"
            "  {\"value\": 0} or {\"value\": 1}\n\n"
            "Display rule:\n"
            "  Relay button state changes only after state topic feedback."
        )
        tk.Label(
            parent,
            text=info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 10),
            justify="left",
            anchor="nw"
        ).place(x=28, y=58, width=500, height=125)

        x_positions = [55, 285]
        y = 200
        idx = 1
        for row in range(4):
            for col in range(2):
                name = f"RY{idx}"
                btn = tk.Button(
                    parent,
                    text=f"{name}: OFF",
                    command=lambda n=name: self.publish_output_state(n),
                    bg="#374151",
                    fg="white",
                    relief="flat",
                    font=("Arial", 15, "bold"),
                    width=14,
                    height=2
                )
                btn.place(x=x_positions[col], y=y)
                self.output_buttons[name] = btn
                idx += 1
            y += 105

        self.update_output_widgets()

    def build_input_section(self, parent):
        info = (
            "State Topic:\n"
            "  digital_input/INx/state\n\n"
            "Payload example:\n"
            "  {\"value\": 0} or {\"value\": 1}\n\n"
            "Display rule:\n"
            "  Active-low input: LOW = ON, HIGH = OFF."
        )
        tk.Label(
            parent,
            text=info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 10),
            justify="left",
            anchor="nw"
        ).place(x=28, y=58, width=500, height=92)

        x_positions = [60, 310]
        y = 170
        idx = 1
        for row in range(4):
            for col in range(2):
                name = f"IN{idx}"
                frame = tk.Frame(parent, bg="#111827", width=180, height=70)
                frame.place(x=x_positions[col], y=y)
                frame.pack_propagate(False)

                title = tk.Label(
                    frame,
                    text=name,
                    bg="#111827",
                    fg="#E5E7EB",
                    font=("Arial", 13, "bold")
                )
                title.pack(side="left", padx=(16, 8))

                state = tk.Label(
                    frame,
                    text="OFF",
                    bg="#374151",
                    fg="white",
                    font=("Arial", 13, "bold"),
                    width=7,
                    pady=8
                )
                state.pack(side="right", padx=(8, 16))
                self.input_labels[name] = state
                idx += 1
            y += 100

        self.update_input_widgets()

    # =====================================================
    # Tab: Analog I/O
    # =====================================================
    def build_analog_io_tab(self):
        wrapper = tk.Frame(self.tab_analog_io, bg="#111827")
        wrapper.pack(fill="both", expand=True, padx=10, pady=10)

        output_card = self.make_card(wrapper, "ANALOG OUTPUT - AO0 TO AO1", 560, 620)
        output_card.pack(side="left", fill="y", padx=(0, 14))

        input_card = self.make_card(wrapper, "ANALOG INPUT - AI0 TO AI3", 560, 620)
        input_card.pack(side="left", fill="both", expand=True)

        self.build_analog_output_section(output_card)
        self.build_analog_input_section(input_card)

    def build_analog_output_section(self, parent):
        info = (
            "Set Topic:\n"
            "  analog_output/AOx/set\n\n"
            "State Topic:\n"
            "  analog_output/AOx/state\n\n"
            "Payload example for both set/state:\n"
            "  {\"voltage\": 1.23}\n\n"
            "Display rule:\n"
            "  AO value boxes update only after state topic feedback."
        )
        tk.Label(
            parent,
            text=info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 10),
            justify="left",
            anchor="nw"
        ).place(x=28, y=58, width=500, height=170)

        y = 260
        for name in self.analog_output_states:
            row = tk.Frame(parent, bg="#111827", width=500, height=105)
            row.place(x=28, y=y)
            row.pack_propagate(False)

            tk.Label(
                row,
                text=name,
                bg="#111827",
                fg="#E5E7EB",
                font=("Arial", 15, "bold")
            ).place(x=18, y=18)

            state_label = tk.Label(
                row,
                text="0.000 V",
                bg="#374151",
                fg="white",
                font=("Consolas", 13, "bold"),
                anchor="center"
            )
            state_label.place(x=90, y=14, width=115, height=36)
            self.analog_output_state_labels[name] = state_label

            voltage_var = tk.StringVar(value="0.0")
            self.analog_output_vars[name] = voltage_var
            self.make_entry(row, voltage_var, 225, 17, 110)

            publish_btn = tk.Button(
                row,
                text="PUBLISH",
                command=lambda n=name: self.publish_analog_output_state(n),
                bg="#10B981",
                fg="white",
                relief="flat",
                font=("Arial", 11, "bold"),
                width=12
            )
            publish_btn.place(x=355, y=14)

            y += 135

        self.update_analog_output_widgets()

    def build_analog_input_section(self, parent):
        info = (
            "State Topic:\n"
            "  analog_input/state\n\n"
            "Payload example:\n"
            "  {\n"
            "    \"AI0\": {\"value\": 12.34, \"mode\": 1},\n"
            "    \"AI1\": {\"value\": 1.23,  \"mode\": 0},\n"
            "    \"AI2\": {\"value\": 0.00,  \"mode\": 0},\n"
            "    \"AI3\": {\"value\": 4.56,  \"mode\": 1}\n"
            "  }\n\n"
            "mode 0 = V, mode 1 = mA. Missing mode keeps previous mode."
        )
        tk.Label(
            parent,
            text=info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 10),
            justify="left",
            anchor="nw"
        ).place(x=28, y=58, width=500, height=210)

        x_positions = [45, 290]
        y = 285
        idx = 0
        for row_idx in range(2):
            for col in range(2):
                name = f"AI{idx}"
                frame = tk.Frame(parent, bg="#111827", width=220, height=100)
                frame.place(x=x_positions[col], y=y)
                frame.pack_propagate(False)

                title = tk.Label(
                    frame,
                    text=name,
                    bg="#111827",
                    fg="#E5E7EB",
                    font=("Arial", 13, "bold")
                )
                title.place(x=16, y=14)

                mode_label = tk.Label(
                    frame,
                    text="--",
                    bg="#374151",
                    fg="white",
                    font=("Arial", 9, "bold"),
                    anchor="center"
                )
                mode_label.place(x=78, y=12, width=118, height=26)
                self.analog_input_mode_labels[name] = mode_label

                value_label = tk.Label(
                    frame,
                    text="0.000 V",
                    bg="#374151",
                    fg="white",
                    font=("Consolas", 13, "bold"),
                    anchor="center"
                )
                value_label.place(x=16, y=52, width=180, height=34)
                self.analog_input_labels[name] = value_label
                idx += 1
            y += 135

        self.update_analog_input_widgets()

    # =====================================================
    # Tab: EEPROM
    # =====================================================
    def build_eeprom_tab(self):
        wrapper = tk.Frame(self.tab_eeprom, bg="#111827")
        wrapper.pack(fill="both", expand=True, padx=10, pady=10)

        write_card = self.make_card(wrapper, "EEPROM WRITE", 650, 620)
        write_card.pack(side="left", fill="both", expand=True, padx=(0, 14))

        read_card = self.make_card(wrapper, "EEPROM READ", 650, 620)
        read_card.pack(side="left", fill="both", expand=True)

        write_info = (
            "Write Topic:\n"
            "  eeprom/write\n\n"
            "Wipe Topic:\n"
            "  eeprom/wipe\n\n"
            "Payload example:\n"
            "  eeprom/write -> UTF-8 bytes from the text box\n"
            "  eeprom/wipe  -> True\n\n"
            "Max write size: 1000 bytes."
        )
        tk.Label(
            write_card,
            text=write_info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 10),
            justify="left",
            anchor="nw"
        ).place(x=28, y=58, width=590, height=120)

        self.eeprom_write_box = ScrolledText(
            write_card,
            bg="#0B1220",
            fg="#F9FAFB",
            insertbackground="white",
            font=("Consolas", 11),
            relief="flat",
            wrap="word"
        )
        self.eeprom_write_box.place(x=28, y=190, width=590, height=265)
        self.eeprom_write_box.bind("<KeyRelease>", self.update_eeprom_write_count)

        self.eeprom_write_count_label = tk.Label(
            write_card,
            text=f"0/{self.eeprom_max_write_bytes} bytes UTF-8",
            bg="#1F2937",
            fg="#9CA3AF",
            font=("Consolas", 11),
            anchor="w"
        )
        self.eeprom_write_count_label.place(x=28, y=468, width=260, height=28)

        write_btn = tk.Button(
            write_card,
            text="WRITE EEPROM",
            command=self.publish_eeprom_write,
            bg="#10B981",
            fg="white",
            relief="flat",
            font=("Arial", 12, "bold"),
            width=18,
            height=2
        )
        write_btn.place(x=28, y=520)

        clear_write_btn = tk.Button(
            write_card,
            text="CLEAR TEXT",
            command=lambda: (self.eeprom_write_box.delete("1.0", "end"), self.update_eeprom_write_count()),
            bg="#374151",
            fg="white",
            relief="flat",
            font=("Arial", 11, "bold"),
            width=14,
            height=2
        )
        clear_write_btn.place(x=240, y=520)

        wipe_btn = tk.Button(
            write_card,
            text="WIPE EEPROM",
            command=self.publish_eeprom_wipe,
            bg="#EF4444",
            fg="white",
            relief="flat",
            font=("Arial", 11, "bold"),
            width=14,
            height=2
        )
        wipe_btn.place(x=415, y=520)

        read_info = (
            "Read Request Topic:\n"
            "  eeprom/read/request\n\n"
            "Read Response Topic:\n"
            "  eeprom/read/response\n\n"
            "Request payload example:\n"
            "  {\"index\": 0}\n\n"
            "Incoming response bytes are decoded as UTF-8 for display."
        )
        tk.Label(
            read_card,
            text=read_info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 10),
            justify="left",
            anchor="nw"
        ).place(x=28, y=58, width=590, height=120)

        tk.Label(
            read_card,
            text="Read Index",
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Arial", 11),
        ).place(x=28, y=190)

        self.eeprom_read_index_var = tk.StringVar(value="0")
        self.make_entry(read_card, self.eeprom_read_index_var, 125, 186, 120)

        self.eeprom_read_box = ScrolledText(
            read_card,
            bg="#0B1220",
            fg="#D1D5DB",
            insertbackground="white",
            font=("Consolas", 11),
            relief="flat",
            wrap="word"
        )
        self.eeprom_read_box.place(x=28, y=230, width=590, height=225)
        self.eeprom_read_box.config(state="disabled")

        self.eeprom_read_info_label = tk.Label(
            read_card,
            text="Last read: --",
            bg="#1F2937",
            fg="#9CA3AF",
            font=("Consolas", 11),
            anchor="w"
        )
        self.eeprom_read_info_label.place(x=28, y=468, width=360, height=28)

        read_btn = tk.Button(
            read_card,
            text="READ EEPROM",
            command=self.request_eeprom_read,
            bg="#2563EB",
            fg="white",
            relief="flat",
            font=("Arial", 12, "bold"),
            width=18,
            height=2
        )
        read_btn.place(x=28, y=520)

        clear_read_btn = tk.Button(
            read_card,
            text="CLEAR READ VIEW",
            command=self.clear_eeprom_read_view,
            bg="#374151",
            fg="white",
            relief="flat",
            font=("Arial", 11, "bold"),
            width=18,
            height=2
        )
        clear_read_btn.place(x=240, y=520)

    def clear_eeprom_read_view(self):
        if hasattr(self, "eeprom_read_box"):
            self.eeprom_read_box.config(state="normal")
            self.eeprom_read_box.delete("1.0", "end")
            self.eeprom_read_box.config(state="disabled")
        if hasattr(self, "eeprom_read_info_label"):
            self.eeprom_read_info_label.config(text="Last read: --")

    # =====================================================
    # Tab: UART
    # =====================================================
    def build_uart_tab(self):
        wrapper = tk.Frame(self.tab_uart, bg="#111827")
        wrapper.pack(fill="both", expand=True, padx=10, pady=10)

        uart_card = self.make_card(wrapper, "UART WRITE / RECEIVE", 1320, 620)
        uart_card.pack(fill="both", expand=True)

        write_info = (
            "Write Topic:\n"
            "  uart/write\n\n"
            "Payload example:\n"
            "  Text box content is encoded as UTF-8 bytes.\n\n"
            "Device action:\n"
            "  device.uart.write(msg)"
        )
        tk.Label(
            uart_card,
            text=write_info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 10),
            justify="left",
            anchor="nw"
        ).place(x=28, y=58, width=560, height=125)

        read_info = (
            "Read Topic:\n"
            "  uart/read\n\n"
            "Incoming payload:\n"
            "  Raw MQTT bytes from the device UART RX side.\n\n"
            "Display rule:\n"
            "  Bytes are decoded as UTF-8 with replacement characters.\n"
            "  SEND = dashboard sent. RECEIVE = device UART RX."
        )
        tk.Label(
            uart_card,
            text=read_info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 10),
            justify="left",
            anchor="nw"
        ).place(x=660, y=58, width=620, height=145)

        tk.Label(
            uart_card,
            text="UART Write",
            bg="#1F2937",
            fg="#F3F4F6",
            font=("Arial", 12, "bold")
        ).place(x=28, y=205)

        self.uart_write_box = ScrolledText(
            uart_card,
            bg="#0B1220",
            fg="#F9FAFB",
            insertbackground="white",
            font=("Consolas", 11),
            relief="flat",
            wrap="word"
        )
        self.uart_write_box.place(x=28, y=235, width=560, height=265)

        send_btn = tk.Button(
            uart_card,
            text="SEND UART",
            command=self.publish_uart_write,
            bg="#10B981",
            fg="white",
            relief="flat",
            font=("Arial", 12, "bold"),
            width=16,
            height=2
        )
        send_btn.place(x=28, y=525)

        clear_send_btn = tk.Button(
            uart_card,
            text="CLEAR TEXT",
            command=self.clear_uart_write_box,
            bg="#374151",
            fg="white",
            relief="flat",
            font=("Arial", 11, "bold"),
            width=14,
            height=2
        )
        clear_send_btn.place(x=220, y=525)

        tk.Label(
            uart_card,
            text="UART Send / Receive History",
            bg="#1F2937",
            fg="#F3F4F6",
            font=("Arial", 12, "bold")
        ).place(x=660, y=205)

        self.uart_history_box = ScrolledText(
            uart_card,
            bg="#0B1220",
            fg="#D1D5DB",
            insertbackground="white",
            font=("Consolas", 11),
            relief="flat",
            wrap="word"
        )
        self.uart_history_box.place(x=660, y=235, width=620, height=265)
        self.uart_history_box.tag_config("send", foreground="#34D399")
        self.uart_history_box.tag_config("receive", foreground="#60A5FA")
        self.uart_history_box.config(state="disabled")

        clear_history_btn = tk.Button(
            uart_card,
            text="CLEAR HISTORY",
            command=self.clear_uart_history,
            bg="#374151",
            fg="white",
            relief="flat",
            font=("Arial", 11, "bold"),
            width=16,
            height=2
        )
        clear_history_btn.place(x=660, y=525)


    # =====================================================
    # Tab: 4G Modem
    # =====================================================
    def build_modem_tab(self):
        wrapper = tk.Frame(self.tab_modem, bg="#111827")
        wrapper.pack(fill="both", expand=True, padx=10, pady=10)

        modem_card = self.make_card(
            wrapper,
            "4G MODEM COMMAND / RECEIVE",
            1320,
            620,
        )
        modem_card.pack(fill="both", expand=True)

        command_info = (
            "Command Topic:\n"
            "  modem/command\n\n"
            "Dashboard payload:\n"
            "  Raw UTF-8 text only (no JSON and no Base64).\n\n"
            "The command box is a multiline text area.\n"
            "Actual newline characters are published unchanged.\n\n"
            "Examples:\n"
            "  AT\n"
            "  AT+CSQ\n"
            "  AT+UART?\n"
        )
        tk.Label(
            modem_card,
            text=command_info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 10),
            justify="left",
            anchor="nw",
        ).place(x=28, y=58, width=570, height=240)

        receive_info = (
            "Receive Topic:\n"
            "  modem/recv\n\n"
            "Device publish form:\n"
            "  {\n"
            "    \"msg\": b2a_base64(buf).strip().decode(\"utf-8\"),\n"
            "    \"work_mode\": 0\n"
            "  }\n\n"
            "Dashboard action:\n"
            "  1. Decode msg from Base64 to original bytes.\n"
            "  2. work_mode 0 shows labelled RX blocks.\n"
            "  3. work_mode > 0 appends directly to terminal.\n"
        )
        tk.Label(
            modem_card,
            text=receive_info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 10),
            justify="left",
            anchor="nw",
        ).place(x=660, y=58, width=620, height=230)


        self.modem_last_received_label = tk.Label(
            modem_card,
            text="Last RX: --",
            bg="#111827",
            fg="#60A5FA",
            font=("Consolas", 10, "bold"),
            anchor="w",
            padx=10,
        )
        self.modem_last_received_label.place(
            x=660,
            y=285,
            width=620,
            height=25,
        )
        
        tk.Label(
            modem_card,
            text="Modem Command (multiline raw text)",
            bg="#1F2937",
            fg="#F3F4F6",
            font=("Arial", 12, "bold"),
        ).place(x=28, y=255)

        self.modem_command_box = ScrolledText(
            modem_card,
            bg="#0B1220",
            fg="#F9FAFB",
            insertbackground="white",
            font=("Consolas", 11),
            relief="flat",
            wrap="word",
        )
        self.modem_command_box.place(
            x=28,
            y=290,
            width=560,
            height=210,
        )

        send_btn = tk.Button(
            modem_card,
            text="SEND MODEM COMMAND",
            command=self.publish_modem_command,
            bg="#10B981",
            fg="white",
            relief="flat",
            font=("Arial", 12, "bold"),
            width=21,
            height=2,
        )
        send_btn.place(x=28, y=525)

        clear_command_btn = tk.Button(
            modem_card,
            text="CLEAR TEXT",
            command=self.clear_modem_command_box,
            bg="#374151",
            fg="white",
            relief="flat",
            font=("Arial", 11, "bold"),
            width=14,
            height=2,
        )
        clear_command_btn.place(x=270, y=525)

        tk.Label(
            modem_card,
            text="Decoded Modem Receive History",
            bg="#1F2937",
            fg="#F3F4F6",
            font=("Arial", 12, "bold"),
        ).place(x=660, y=255)


        self.modem_history_box = ScrolledText(
            modem_card,
            bg="#0B1220",
            fg="#D1D5DB",
            insertbackground="white",
            font=("Consolas", 11),
            relief="flat",
            wrap="word",
        )
        self.modem_history_box.place(
            x=660,
            y=320,
            width=620,
            height=220,
        )
        self.modem_history_box.tag_config("send", foreground="#34D399")
        self.modem_history_box.tag_config("receive", foreground="#60A5FA")
        self.modem_history_box.tag_config("stream_send", foreground="#F59E0B")
        self.modem_history_box.tag_config("stream_receive", foreground="#F59E0B")
        self.modem_history_box.config(state="disabled")

        clear_history_btn = tk.Button(
            modem_card,
            text="CLEAR HISTORY",
            command=self.clear_modem_history,
            bg="#374151",
            fg="white",
            relief="flat",
            font=("Arial", 11, "bold"),
            width=16,
            height=2,
        )
        clear_history_btn.place(x=660, y=555)

    # =====================================================
    # Tab: CAN
    # =====================================================
    def build_can_tab(self):
        wrapper = tk.Frame(self.tab_can, bg="#111827")
        wrapper.pack(fill="both", expand=True, padx=10, pady=10)

        can_card = self.make_card(wrapper, "CAN SEND / RECEIVE", 1320, 620)
        can_card.pack(fill="both", expand=True)

        send_info = (
            "Send Topic:\n"
            "  CAN/send\n\n"
            "Direction:\n"
            "  Dashboard -> MQTT broker -> device -> CAN bus\n\n"
            "Payload form:\n"
            "  {\"id\": \"0x123\", \"msg\": \"0x112233\"}\n\n"
            "Enter data as hexadecimal bytes, for example:\n"
            "  01 02 A0 FF"
        )
        tk.Label(
            can_card,
            text=send_info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 10),
            justify="left",
            anchor="nw"
        ).place(x=28, y=58, width=570, height=180)

        recv_info = (
            "Receive Topic:\n"
            "  CAN/recv\n\n"
            "Direction:\n"
            "  Device CAN bus -> MQTT broker -> dashboard\n\n"
            "Expected payload form:\n"
            "  {\"frame_type\": \"standard\", \"id\": \"0x0\",\n"
            "   \"data\": \"1c98ccba0000\"}\n\n"
            "Incoming frames are shown as both hexadecimal bytes\n"
            "and a Python bytes representation."
        )
        tk.Label(
            can_card,
            text=recv_info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 10),
            justify="left",
            anchor="nw"
        ).place(x=660, y=58, width=620, height=180)

        self.can_last_received_label = tk.Label(
            can_card,
            text="Last RX: --",
            bg="#111827",
            fg="#60A5FA",
            font=("Consolas", 10, "bold"),
            anchor="w",
            padx=10
        )
        self.can_last_received_label.place(x=660, y=225, width=620, height=25)

        tk.Label(
            can_card,
            text="CAN ID",
            bg="#1F2937",
            fg="#F3F4F6",
            font=("Arial", 12, "bold")
        ).place(x=28, y=255)

        self.can_id_var = tk.StringVar(value="0x123")
        self.make_entry(can_card, self.can_id_var, 120, 250, 180)

        tk.Label(
            can_card,
            text="CAN Data (hex, max 8 bytes)",
            bg="#1F2937",
            fg="#F3F4F6",
            font=("Arial", 12, "bold")
        ).place(x=28, y=305)

        self.can_data_box = ScrolledText(
            can_card,
            bg="#0B1220",
            fg="#F9FAFB",
            insertbackground="white",
            font=("Consolas", 12),
            relief="flat",
            wrap="word"
        )
        self.can_data_box.place(x=28, y=338, width=560, height=125)
        self.can_data_box.insert("1.0", "0x112233")

        send_btn = tk.Button(
            can_card,
            text="SEND CAN",
            command=self.publish_can_message,
            bg="#10B981",
            fg="white",
            relief="flat",
            font=("Arial", 12, "bold"),
            width=16,
            height=2
        )
        send_btn.place(x=28, y=500)

        clear_data_btn = tk.Button(
            can_card,
            text="CLEAR DATA",
            command=self.clear_can_data_box,
            bg="#374151",
            fg="white",
            relief="flat",
            font=("Arial", 11, "bold"),
            width=14,
            height=2
        )
        clear_data_btn.place(x=220, y=500)

        tk.Label(
            can_card,
            text="CAN Send / Receive History",
            bg="#1F2937",
            fg="#F3F4F6",
            font=("Arial", 12, "bold")
        ).place(x=660, y=255)

        self.can_history_box = ScrolledText(
            can_card,
            bg="#0B1220",
            fg="#D1D5DB",
            insertbackground="white",
            font=("Consolas", 11),
            relief="flat",
            wrap="word"
        )
        self.can_history_box.place(x=660, y=290, width=620, height=250)
        self.can_history_box.tag_config("send", foreground="#34D399")
        self.can_history_box.tag_config("receive", foreground="#60A5FA")
        self.can_history_box.config(state="disabled")

        clear_history_btn = tk.Button(
            can_card,
            text="CLEAR HISTORY",
            command=self.clear_can_history,
            bg="#374151",
            fg="white",
            relief="flat",
            font=("Arial", 11, "bold"),
            width=16,
            height=2
        )
        clear_history_btn.place(x=660, y=555)

    # =====================================================
    # Helper widgets
    # =====================================================
    def make_card(self, parent, title, width, height):
        frame = tk.Frame(parent, bg="#1F2937", width=width, height=height, bd=0, highlightthickness=0)
        frame.pack_propagate(False)

        title_label = tk.Label(
            frame,
            text=title,
            bg="#1F2937",
            fg="#F3F4F6",
            font=("Arial", 13, "bold")
        )
        title_label.place(x=18, y=14)

        top_line = tk.Frame(frame, bg="#374151", height=1, width=width - 36)
        top_line.place(x=18, y=40)

        return frame

    def make_label(self, parent, text, x, y):
        label = tk.Label(parent, text=text, bg="#1F2937", fg="#D1D5DB", font=("Arial", 11))
        label.place(x=x, y=y)
        return label

    def make_entry(self, parent, variable, x, y, width):
        entry = tk.Entry(
            parent,
            textvariable=variable,
            bg="#111827",
            fg="#F9FAFB",
            insertbackground="white",
            relief="flat",
            font=("Consolas", 11)
        )
        entry.place(x=x, y=y, width=width, height=30)
        return entry

    # =====================================================
    # UI updates
    # =====================================================
    def update_network_mode_widgets(self, *_args):
        if not hasattr(self, "net_config_entries"):
            return

        is_dhcp = self.net_mode.get() == "dhcp"
        entry_state = "disabled" if is_dhcp else "normal"

        for entry in self.net_config_entries:
            entry.config(
                state=entry_state,
                disabledbackground="#374151",
                disabledforeground="#9CA3AF",
            )

    def init_status_widgets(self):
        self.update_connection_badge("DISCONNECTED", "#EF4444")
        if hasattr(self, "connection_value"):
            self.connection_value.config(text="Disconnected")

    def update_connection_badge(self, text, color):
        if hasattr(self, "connection_badge"):
            self.connection_badge.config(text=text, bg=color)

    def update_output_widgets(self):
        for name, button in self.output_buttons.items():
            state = self.output_states.get(name, False)
            if state:
                button.config(text=f"{name}: ON", bg="#22C55E", fg="#052E16")
            else:
                button.config(text=f"{name}: OFF", bg="#374151", fg="white")

    def update_input_widgets(self):
        for name, label in self.input_labels.items():
            pin_is_high = self.input_states.get(name, False)
            
            # Active-low input:
            # HIGH means OFF, LOW means ON.
            logical_on = not pin_is_high
            
            if logical_on:
                label.config(text="ON", bg="#3B82F6", fg="#0F172A")
            else:
                label.config(text="OFF", bg="#374151", fg="white")

    def update_analog_input_widgets(self):
        for name, label in self.analog_input_labels.items():
            value = self.analog_input_states.get(name, 0.0)
            mode = self.analog_input_modes.get(name, 0)

            if mode == 1:
                unit = "mA"
                mode_text = "CURRENT MODE"
                bg = "#F59E0B"
                fg = "#111827"
                mode_bg = "#D97706"
            else:
                unit = "V"
                mode_text = "VOLTAGE MODE"
                bg = "#3B82F6"
                fg = "#0F172A"
                mode_bg = "#2563EB"

            label.config(text=f"{value:.3f} {unit}", bg=bg, fg=fg)

            mode_label = self.analog_input_mode_labels.get(name)
            if mode_label is not None:
                mode_label.config(text=mode_text, bg=mode_bg, fg="white")

    def update_analog_output_widgets(self):
        for name, label in self.analog_output_state_labels.items():
            voltage = self.analog_output_states.get(name, 0.0)
            label.config(text=f"{voltage:.3f} V", bg="#22C55E", fg="#052E16")
            if name in self.analog_output_vars:
                self.analog_output_vars[name].set(f"{voltage:.3f}")

    def write_log(self, text):
        if self.closing or not hasattr(self, "log_box"):
            return
        try:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            self.log_box.insert("end", f"[{timestamp}] {text}\n")
            self.log_box.see("end")
        except tk.TclError:
            pass

    def update_clock(self):
        if self.closing:
            return
        try:
            now = time.strftime("%H:%M:%S")
            self.clock_label.config(text=now)
            self.root.after(1000, self.update_clock)
        except tk.TclError:
            pass

    def on_close(self):
        if self.closing:
            return

        self.closing = True
        self.connected = False

        try:
            if self.client is not None:
                self.client.on_connect = None
                self.client.on_message = None
                self.client.on_disconnect = None
                self.client.disconnect()
                self.client.loop_stop()
        except Exception:
            pass

        try:
            self.root.destroy()
        except tk.TclError:
            pass


if __name__ == "__main__":
    root = tk.Tk()
    app = DeviceDashboard(root)
    root.mainloop()
