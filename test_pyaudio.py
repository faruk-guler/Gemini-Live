import pyaudio

try:
    pya = pyaudio.PyAudio()
    info = pya.get_default_input_device_info()
    print("Default input device info:", info)
    print("Trying get_default_info_by_index...")
    pya.get_default_info_by_index(info["index"])
except Exception as e:
    print("Error:", e)
