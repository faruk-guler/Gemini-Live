"""
Gemini Live Translator - Sadece Ses (Kamera Kapalı, Türkçe -> İngilizce)
Bağımlılıklar: pip install google-genai pyaudio
"""

import os
import asyncio
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText
import struct
import math
import wave
import traceback
import datetime

import pyaudio
from google import genai
from google.genai import types

# Ses & Model Ayarları
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024
MODEL = "models/gemini-3.5-live-translate-preview"

# Arayüz ile arka plan iletişimi için kuyruk
gui_queue = queue.Queue()

# API Anahtarı Doğrulama
api_key = os.environ.get("GEMINI_API_KEY")
if api_key:
    api_key = api_key.strip().replace('"', '').replace("'", "")

client = None
if api_key and len(api_key) > 10:
    client = genai.Client(
        http_options={"api_version": "v1beta"},
        api_key=api_key,
    )
else:
    api_key = None


# Dosya yollarını sabitlemek için script dizinini al
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

class LiveAudioTranslator:
    """Türkçe ses girişini sadece arka planda sessizce İngilizceye çevirip kaydeden sınıf."""
    def __init__(self):
        self.target_lang = "English"
        # Her oturum için benzersiz zaman damgası
        self.timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Çıktı dosya yollarını bu scriptin klasörüyle ilişkilendir
        self.transcript_path = os.path.join(BASE_DIR, f"translated_transcript_{self.timestamp}.txt")
        self.audio_path = os.path.join(BASE_DIR, f"translated_audio_{self.timestamp}.wav")
        
        self.audio_in_queue = asyncio.Queue()
        self.out_queue = asyncio.Queue(maxsize=5)
        self.session = None
        self.stop_event = asyncio.Event()
        self.audio_stream = None

    async def send_realtime(self):
        """Kuyruktan gelen mikrofon verisini Gemini'a gönderir."""
        while not self.stop_event.is_set():
            msg = await self.out_queue.get()
            if self.session is not None:
                await self.session.send_realtime_input(
                    audio=types.Blob(data=msg["data"], mime_type=msg["mime_type"])
                )

    async def listen_audio(self):
        """Mikrofondan sesi saniye saniye dinleyip kaydeder."""
        pya = pyaudio.PyAudio()
        mic_info = pya.get_default_input_device_info()
        self.audio_stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            input=True,
            input_device_index=mic_info["index"],
            frames_per_buffer=CHUNK_SIZE,
        )
        kwargs = {"exception_on_overflow": False} if __debug__ else {}
        
        while not self.stop_event.is_set():
            data = await asyncio.to_thread(self.audio_stream.read, CHUNK_SIZE, **kwargs)
            
            # Canlı ses seviyesi göstergesi için RMS hesabı
            if len(data) > 0:
                count = len(data) // 2
                shorts = struct.unpack(f"{count}h", data)
                rms = math.sqrt(sum((s / 32768.0) ** 2 for s in shorts) / max(1, count))
                gui_queue.put({"type": "volume", "val": rms})

            await self.out_queue.put({"type": "audio", "data": data, "mime_type": "audio/pcm;rate=16000"})
        
        self.audio_stream.close()
        pya.terminate()

    async def receive_audio(self):
        """Gemini'dan gelen sesleri ve metin çevirilerini yakalar."""
        while not self.stop_event.is_set():
            if self.session is not None:
                turn = self.session.receive()
                has_text = False
                async for response in turn:
                    if data := response.data:
                        self.audio_in_queue.put_nowait(data)
                        gui_queue.put({"type": "status", "val": "Gemini Konuşuyor..."})
                        continue
                    if text := response.text:
                        gui_queue.put({"type": "transcript", "val": text})
                        gui_queue.put({"type": "status", "val": "Gemini Çeviriyor..."})
                        
                        # Metni anında zaman damgalı TXT dosyasına kaydet
                        try:
                            with open(self.transcript_path, "a", encoding="utf-8") as f:
                                f.write(text)
                        except Exception:
                            pass
                        has_text = True

                if has_text:
                    try:
                        with open(self.transcript_path, "a", encoding="utf-8") as f:
                            f.write("\n")
                    except Exception:
                        pass

                while not self.audio_in_queue.empty():
                    self.audio_in_queue.get_nowait()
                gui_queue.put({"type": "status", "val": "Dinleniyor..."})

    async def play_audio(self):
        """Gemini'dan gelen sesleri hoparlöre vermeden sadece zaman damgalı WAV dosyasına kaydeder."""
        wf = wave.open(self.audio_path, "wb")
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # 16-bit PCM = 2 bytes
        wf.setframerate(RECEIVE_SAMPLE_RATE)
        
        try:
            while not self.stop_event.is_set():
                bytestream = await self.audio_in_queue.get()
                # Hoparlöre aktarmadan doğrudan diske yazıyoruz (sessiz kayıt)
                await asyncio.to_thread(wf.writeframes, bytestream)
        finally:
            wf.close()

    async def run(self):
        """Ana bağlantı ve görev yönetim merkezi."""
        try:
            with open(self.transcript_path, "w", encoding="utf-8") as f:
                f.write(f"--- CANLI ÇEVİRİ GÜNLÜĞÜ ---\nHedef Dil: English\nTarih: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        except Exception:
            pass

        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            translation_config=types.TranslationConfig(
                target_language_code="en"
            )
        )

        try:
            gui_queue.put({"type": "status", "val": "Bağlantı kuruluyor..."})
            async with (
                client.aio.live.connect(model=MODEL, config=config) as session,
                asyncio.TaskGroup() as tg,
            ):
                self.session = session
                gui_queue.put({"type": "status", "val": "Bağlantı aktif. Konuşabilirsiniz."})

                tg.create_task(self.send_realtime())
                tg.create_task(self.listen_audio())
                tg.create_task(self.receive_audio())
                tg.create_task(self.play_audio())

                await self.stop_event.wait()
                
        except Exception as e:
            gui_queue.put({"type": "error", "val": str(e)})
            traceback.print_exc()
        finally:
            gui_queue.put({"type": "status", "val": "Bağlantı Sonlandırıldı"})


class TranslatorGUI:
    """Tkinter arayüzünün yönetildiği sınıf."""
    def __init__(self, root):
        self.root = root
        self.root.title("Gemini Türkçe -> İngilizce Canlı Çevirmen")
        self.root.geometry("500x440")
        self.root.configure(bg="#121212")

        self.loop_thread = None
        self.audio_loop = None
        self.async_loop = None

        self.setup_styles()
        self.create_widgets()
        self.root.after(100, self.poll_queue)

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background="#121212", foreground="#ffffff")
        style.configure("TLabel", background="#121212", foreground="#ffffff", font=("Segoe UI", 10))
        style.configure("Accent.TButton", background="#007acc", foreground="#ffffff", borderwidth=0, font=("Segoe UI Bold", 11))
        style.map("Accent.TButton", background=[("active", "#0098ff")])
        style.configure("Stop.TButton", background="#d9534f", foreground="#ffffff", borderwidth=0, font=("Segoe UI Bold", 11))
        style.map("Stop.TButton", background=[("active", "#c9302c")])

    def create_widgets(self):
        tk.Label(self.root, text="Gemini Türkçe -> İngilizce Çevirmen", font=("Segoe UI Semibold", 15), bg="#121212", fg="#007acc").pack(pady=15)

        # Bilgi Satırı
        info_frame = tk.Frame(self.root, bg="#121212")
        info_frame.pack(fill="x", padx=20, pady=5)
        tk.Label(info_frame, text="Çeviri Yönü:", bg="#121212", fg="#888888").pack(side="left", padx=5)
        tk.Label(info_frame, text="Türkçe ➔ İngilizce (Sessiz Kayıt)", font=("Segoe UI Bold", 10), bg="#121212", fg="#2ecc71").pack(side="left", padx=5)

        # Butonlar
        btn_frame = tk.Frame(self.root, bg="#121212")
        btn_frame.pack(fill="x", padx=20, pady=10)
        
        self.start_btn = ttk.Button(btn_frame, text="BAŞLAT", style="Accent.TButton", command=self.start_session)
        self.start_btn.pack(side="left", fill="x", expand=True, padx=5)
        
        self.stop_btn = ttk.Button(btn_frame, text="DURDUR", style="Stop.TButton", command=self.stop_session, state="disabled")
        self.stop_btn.pack(side="right", fill="x", expand=True, padx=5)

        # Ses Seviyesi Barı
        self.vol_canvas = tk.Canvas(self.root, height=8, bg="#1e1e1e", highlightthickness=0)
        self.vol_canvas.pack(fill="x", padx=20, pady=5)
        self.vol_bar = self.vol_canvas.create_rectangle(0, 0, 0, 8, fill="#2ecc71")

        # Metin Log Alanı
        self.text_area = ScrolledText(self.root, height=12, bg="#1e1e1e", fg="#ffffff", font=("Consolas", 10), wrap="word", highlightthickness=0)
        self.text_area.pack(fill="both", expand=True, padx=20, pady=10)
        self.text_area.insert("end", "Sistem Hazır. Başlat butonuna basarak Türkçe konuşmaya başlayabilirsiniz...\n")
        self.text_area.configure(state="disabled")

        # Alt Durum Çubuğu
        self.status_lbl = tk.Label(self.root, text="Durum: Çevrimdışı", bd=1, relief="sunken", anchor="w", bg="#1e1e1e", fg="#aaaaaa", font=("Segoe UI", 9))
        self.status_lbl.pack(fill="x", side="bottom")

    def log(self, text):
        self.text_area.configure(state="normal")
        self.text_area.insert("end", text)
        self.text_area.see("end")
        self.text_area.configure(state="disabled")

    def start_session(self):
        if not api_key:
            messagebox.showerror("Hata", "GEMINI_API_KEY bulunamadı.")
            return

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        
        self.log("\n--- Yeni Oturum (Türkçe ➔ İngilizce) ---\n")

        self.audio_loop = LiveAudioTranslator()
        self.loop_thread = threading.Thread(target=self.run_async_loop, daemon=True)
        self.loop_thread.start()

    def run_async_loop(self):
        self.async_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.async_loop)
        self.async_loop.run_until_complete(self.audio_loop.run())

    def stop_session(self):
        if self.audio_loop and self.async_loop:
            self.async_loop.call_soon_threadsafe(self.audio_loop.stop_event.set)
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.vol_canvas.coords(self.vol_bar, 0, 0, 0, 8)

    def on_closing(self):
        """Pencere kapatıldığında ses dosyalarının bozulmaması için güvenli çıkış yapar."""
        self.stop_session()
        if self.loop_thread and self.loop_thread.is_alive():
            self.loop_thread.join(timeout=2.0)
        self.root.destroy()

    def poll_queue(self):
        while not gui_queue.empty():
            try:
                msg = gui_queue.get_nowait()
                if msg["type"] == "status":
                    self.status_lbl.configure(text=f"Durum: {msg['val']}")
                elif msg["type"] == "transcript":
                    self.log(msg["val"])
                elif msg["type"] == "volume":
                    width = self.vol_canvas.winfo_width()
                    if width < 10:
                        width = 460
                    new_x = min(width, int(msg["val"] * width * 4.0))
                    self.vol_canvas.coords(self.vol_bar, 0, 0, new_x, 8)
                elif msg["type"] == "error":
                    self.log(f"\nHata Oluştu: {msg['val']}\n")
                    self.stop_session()
            except queue.Empty:
                break
        self.root.after(50, self.poll_queue)


if __name__ == "__main__":
    root = tk.Tk()
    app = TranslatorGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
