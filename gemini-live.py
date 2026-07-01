"""
Gemini Live Translator - Sadece Ses (Kamera Kapalı, Türkçe -> İngilizce)
Bağımlılıklar: pip install google-genai pyaudio
"""

from os import environ
import asyncio
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText
import array
import math
import wave
import datetime
from pathlib import Path
import webbrowser

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

# API Anahtarı Doğrulama (Başlangıçta çevre değişkeninden oku)
DEFAULT_API_KEY = environ.get("GEMINI_API_KEY", "")
if DEFAULT_API_KEY:
    DEFAULT_API_KEY = DEFAULT_API_KEY.strip().replace('"', '').replace("'", "")


# Dosya yollarını sabitlemek için script dizinini al
BASE_DIR = Path(__file__).resolve().parent

class LiveAudioTranslator:
    """Türkçe ses girişini sadece arka planda sessizce İngilizceye çevirip kaydeden sınıf."""
    def __init__(self, client, gui_queue):
        self.client = client
        self.gui_queue = gui_queue
        # Her oturum için benzersiz zaman damgası
        timestamp = f"{datetime.datetime.now():%Y%m%d_%H%M%S}"
        
        # Çıktı dosya yollarını bu scriptin klasörüyle ilişkilendir
        self.transcript_path = BASE_DIR / f"translated_transcript_{timestamp}.txt"
        self.audio_path = BASE_DIR / f"translated_audio_{timestamp}.wav"
        
        self.audio_in_queue = asyncio.Queue()
        self.out_queue = asyncio.Queue(maxsize=5)
        self.session = None
        self.stop_event = asyncio.Event()
        self.loop = None
        self.audio_stream = None
        self.send_task = None
        self.listen_task = None
        self.receive_task = None
        self.play_task = None
        self.transcript_file_initialized = False
 
    def _write_transcript_sync(self, text, is_header=False):
        """Metin kaydını diske yazan senkron yardımcı fonksiyon."""
        try:
            mode = "w" if is_header else "a"
            with open(self.transcript_path, mode, encoding="utf-8") as f:
                if is_header:
                    f.write(f"--- CANLI ÇEVİRİ GÜNLÜĞÜ ---\nHedef Dil: English\nTarih: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
                else:
                    f.write(text)
        except Exception:
            pass

    def stop(self):
        """Çeviri döngüsünü thread-safe şekilde durdurur."""
        if self.loop:
            try:
                self.loop.call_soon_threadsafe(self.stop_event.set)
            except Exception:
                pass

    async def send_realtime(self):
        """Kuyruktan gelen mikrofon verisini Gemini'a gönderir."""
        try:
            while not self.stop_event.is_set():
                msg = await self.out_queue.get()
                if self.session is not None:
                    await self.session.send_realtime_input(
                        audio=types.Blob(data=msg["data"], mime_type=msg["mime_type"])
                    )
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def listen_audio(self):
        """Mikrofondan sesi saniye saniye dinleyip kaydeder."""
        pya = pyaudio.PyAudio()
        try:
            # Varsayılan mikrofonu doğrula
            pya.get_default_input_device_info()
        except Exception as e:
            self.gui_queue.put({"type": "error", "val": f"Mikrofon bulunamadı: {e}"})
            pya.terminate()
            return

        try:
            self.audio_stream = await asyncio.to_thread(
                pya.open,
                format=FORMAT,
                channels=CHANNELS,
                rate=SEND_SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )
        except Exception as e:
            self.gui_queue.put({"type": "error", "val": f"Mikrofon açılamadı: {e}"})
            pya.terminate()
            return
            
        try:
            while not self.stop_event.is_set():
                data = await asyncio.to_thread(self.audio_stream.read, CHUNK_SIZE, exception_on_overflow=False)
                
                # Canlı ses seviyesi göstergesi için RMS hesabı
                if len(data) > 0:
                    # Sınır taşmalarını önlemek için 16-bit (2 bayt) hizala
                    data = data[:(len(data) // 2) * 2]
                    # C seviyesinde hızlı ve güvenli dönüşüm (struct yerine array)
                    shorts = array.array('h', data)
                    sq_sum = sum(s * s for s in shorts)
                    rms = math.sqrt(sq_sum / max(1, len(shorts))) / 32768.0
                    self.gui_queue.put({"type": "volume", "val": rms})

                await self.out_queue.put({"type": "audio", "data": data, "mime_type": "audio/pcm;rate=16000"})
        except asyncio.CancelledError:
            pass
        finally:
            if self.audio_stream:
                try:
                    self.audio_stream.close()
                except Exception:
                    pass
            pya.terminate()

    async def receive_audio(self):
        """Gemini'dan gelen sesleri ve metin çevirilerini yakalar."""
        try:
            while not self.stop_event.is_set():
                if self.session is not None:
                    turn = self.session.receive()
                    async for response in turn:
                        if data := response.data:
                            self.audio_in_queue.put_nowait(data)
                            self.gui_queue.put({"type": "status", "val": "Gemini Konuşuyor..."})
                            
                        text_val = response.text
                        if not text_val and getattr(response, "server_content", None) and getattr(response.server_content, "output_transcription", None):
                            text_val = response.server_content.output_transcription.text

                        if text_val:
                            self.gui_queue.put({"type": "transcript", "val": text_val})
                            self.gui_queue.put({"type": "status", "val": "Gemini Çeviriyor..."})
                            
                            # İlk veri geldiğinde dosyayı oluştur ve başlığı yaz
                            if not self.transcript_file_initialized:
                                await asyncio.to_thread(self._write_transcript_sync, "", is_header=True)
                                self.transcript_file_initialized = True

                            # Metni anında zaman damgalı TXT dosyasına kaydet
                            await asyncio.to_thread(self._write_transcript_sync, text_val)

                        # Eğer Gemini konuşma sırasını (turn) bitirdiyse, metin dosyasına ve arayüze bir alt satır ekle
                        if getattr(response, "server_content", None) and getattr(response.server_content, "turn_complete", False):
                            if self.transcript_file_initialized:
                                await asyncio.to_thread(self._write_transcript_sync, "\n")
                            self.gui_queue.put({"type": "transcript", "val": "\n"})

                    self.gui_queue.put({"type": "status", "val": "Dinleniyor..."})
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def play_audio(self):
        """Gemini'dan gelen sesleri hoparlöre vermeden sadece zaman damgalı WAV dosyasına kaydeder."""
        wf = None

        def write_frame(data):
            nonlocal wf
            if wf is None:
                # Path nesnesini bazi Python surumlerinde wave kütüphanesine string olarak vermek gerekir.
                wf = wave.open(str(self.audio_path), "wb")
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)  # 16-bit PCM = 2 bytes
                wf.setframerate(RECEIVE_SAMPLE_RATE)
            wf.writeframes(data)

        try:
            while not self.stop_event.is_set():
                bytestream = await self.audio_in_queue.get()
                await asyncio.to_thread(write_frame, bytestream)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            # Oturum kapandığında kuyrukta biriken son ses paketlerini de diske yazalım
            try:
                while not self.audio_in_queue.empty():
                    bytestream = self.audio_in_queue.get_nowait()
                    write_frame(bytestream)
            except Exception:
                pass
            if wf is not None:
                wf.close()

    async def run(self):
        """Ana bağlantı ve görev yönetim merkezi."""
        self.loop = asyncio.get_running_loop()
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            translation_config=types.TranslationConfig(
                target_language_code="en"
            ),
            output_audio_transcription=types.AudioTranscriptionConfig()
        )

        try:
            self.gui_queue.put({"type": "status", "val": "Bağlantı kuruluyor..."})
            async with self.client.aio.live.connect(model=MODEL, config=config) as session:
                self.session = session
                self.gui_queue.put({"type": "status", "val": "Kayıt başladı. Konuşabilirsiniz."})

                # Görevleri asenkron olarak başlat
                self.send_task = asyncio.create_task(self.send_realtime())
                self.listen_task = asyncio.create_task(self.listen_audio())
                self.receive_task = asyncio.create_task(self.receive_audio())
                self.play_task = asyncio.create_task(self.play_audio())

                await self.stop_event.wait()
                
        except Exception as e:
            self.gui_queue.put({"type": "error", "val": str(e)})
        finally:
            # Görevleri sonlandır ve bekle
            tasks = [t for t in [self.send_task, self.listen_task, self.receive_task, self.play_task] if t and not t.done()]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                
            self.gui_queue.put({"type": "status", "val": "Bağlantı sonlandırıldı. Dosyalar kaydedildi."})
            self.gui_queue.put({"type": "terminated"})


class TranslatorGUI:
    """Tkinter arayüzünün yönetildiği sınıf."""
    def __init__(self, root):
        self.root = root
        self.root.title("Gemini Live Türkçe -> İngilizce")
        self.root.geometry("500x460")
        self.root.configure(bg="#121212")

        self.gui_queue = queue.Queue()
        self.loop_thread = None
        self.audio_loop = None

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
        # Alt Durum Çubuğu (En altta kalması için text_area'dan ÖNCE side="bottom" ile paketlenmeli)
        self.status_lbl = tk.Label(self.root, text="Durum: Hazır", bd=1, relief="sunken", anchor="w", bg="#1e1e1e", fg="#aaaaaa", font=("Segoe UI", 9))
        self.status_lbl.pack(fill="x", side="bottom")

        # Hakkında Kısmı (Durum çubuğunun hemen üstünde kalması için side="bottom" ile paketliyoruz)
        about_frame = tk.Frame(self.root, bg="#121212")
        about_frame.pack(fill="x", side="bottom", padx=20, pady=(0, 10))
        
        tk.Label(about_frame, text="Hakkında:", bg="#121212", fg="#888888", font=("Segoe UI", 9, "bold")).pack(side="left")
        
        link1 = tk.Label(about_frame, text="www.farukguler.com", bg="#121212", fg="#007acc", font=("Segoe UI", 9, "underline"), cursor="hand2")
        link1.pack(side="left", padx=10)
        link1.bind("<Button-1>", lambda e: webbrowser.open("http://www.farukguler.com"))
        
        link2 = tk.Label(about_frame, text="github.com/faruk-guler", bg="#121212", fg="#007acc", font=("Segoe UI", 9, "underline"), cursor="hand2")
        link2.pack(side="left", padx=5)
        link2.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/faruk-guler"))

        tk.Label(self.root, text="Gemini Live Türkçe -> İngilizce", font=("Segoe UI Semibold", 15), bg="#121212", fg="#007acc").pack(pady=15)

        # API Anahtarı Giriş Alanı
        api_frame = tk.Frame(self.root, bg="#121212")
        api_frame.pack(fill="x", padx=20, pady=5)
        tk.Label(api_frame, text="Gemini API Anahtarı:", bg="#121212", fg="#888888").pack(side="left", padx=5)
        
        self.show_key = False
        self.api_key_entry = tk.Entry(
            api_frame, 
            bg="#1e1e1e", 
            fg="#ffffff", 
            insertbackground="#ffffff", 
            relief="flat", 
            font=("Segoe UI", 10),
            show="*"
        )
        self.api_key_entry.pack(side="left", fill="x", expand=True, padx=5)
        
        # Eğer sistemde çevre değişkeni varsa doldur
        if DEFAULT_API_KEY:
            self.api_key_entry.insert(0, DEFAULT_API_KEY)
            
        self.toggle_btn = tk.Button(
            api_frame, 
            text="👁", 
            bg="#1e1e1e", 
            fg="#ffffff", 
            relief="flat", 
            activebackground="#2a2a2a", 
            activeforeground="#ffffff",
            font=("Segoe UI", 9),
            command=self.toggle_api_key_visibility
        )
        self.toggle_btn.pack(side="right", padx=5)

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

        # Basit Dalga (Waveform) Animasyonu
        self.vol_canvas = tk.Canvas(self.root, height=40, bg="#1e1e1e", highlightthickness=0)
        self.vol_canvas.pack(fill="x", padx=20, pady=10)
        self.num_bars = 20
        self.wave_bars = []
        
        # Hedef ve anlık ses seviyeleri
        self.target_volume = 0.0
        self.current_volume = 0.0
        self.animation_idle = True

        # Başlangıçta boş çizgiler oluştur (Update fonksiyonunda hizalanacaklar)
        for i in range(self.num_bars):
            bar = self.vol_canvas.create_line(0, 0, 0, 0, fill="#2ecc71", width=4, capstyle=tk.ROUND)
            self.wave_bars.append(bar)

        # Metin Log Alanı (Kalan boşluğu doldurması için expand=True)
        self.text_area = ScrolledText(self.root, height=12, bg="#1e1e1e", fg="#ffffff", font=("Consolas", 10), wrap="word", highlightthickness=0, padx=10, pady=10)
        self.text_area.pack(fill="both", expand=True, padx=20, pady=10)
        self.text_area.insert("end", "Sistem Hazır. Başlat butonuna basarak Türkçe konuşmaya başlayabilirsiniz...\n")
        self.text_area.configure(state="disabled")

    def update_animation(self):
        """Basit dalga animasyonunu günceller."""
        # Sessizlik durumunda hedefi yavaşça azalt
        self.target_volume = max(0.0, self.target_volume - 0.08)
        
        # CPU/GPU Optimizasyonu: Eğer ses tamamen sıfırlandıysa döngüyü sonlandır
        if self.target_volume == 0.0 and self.current_volume < 0.01:
            self.current_volume = 0.0
            self.animation_idle = True
            
            # Son kez barları 4px yüksekliğe sıfırla
            width = self.vol_canvas.winfo_width()
            height = self.vol_canvas.winfo_height()
            if width > 10:
                center_x = width / 2
                bar_spacing = min(15, width / self.num_bars)
                start_x = center_x - (self.num_bars * bar_spacing) / 2
                for i in range(self.num_bars):
                    x = start_x + (i * bar_spacing)
                    y1 = height / 2 - 2
                    y2 = height / 2 + 2
                    self.vol_canvas.coords(self.wave_bars[i], x, y1, x, y2)
            return  # Döngü sonlandı!
            
        # Yumuşak geçiş (Easing)
        self.current_volume += (self.target_volume - self.current_volume) * 0.3
        
        width = self.vol_canvas.winfo_width()
        height = self.vol_canvas.winfo_height()
        if width > 10:
            center_x = width / 2
            bar_spacing = min(15, width / self.num_bars)
            start_x = center_x - (self.num_bars * bar_spacing) / 2
            
            for i in range(self.num_bars):
                # Merkezde yüksek, kenarlara doğru azalan basit üçgen (V) formu
                curve = 1.0 - abs(i - self.num_bars / 2) / (self.num_bars / 2)
                curve = max(0.1, curve)
                
                # Maksimum bar yüksekliği
                max_h = height * 0.8
                
                # Bar yüksekliğini hesapla
                bar_h = 4 + (max_h * self.current_volume * curve)
                
                x = start_x + (i * bar_spacing)
                y1 = height / 2 - bar_h / 2
                y2 = height / 2 + bar_h / 2
                
                self.vol_canvas.coords(self.wave_bars[i], x, y1, x, y2)
                
        self.root.after(30, self.update_animation)

    def log(self, text):
        self.text_area.configure(state="normal")
        self.text_area.insert("end", text)
        self.text_area.see("end")
        self.text_area.configure(state="disabled")

    def toggle_api_key_visibility(self):
        self.show_key = not self.show_key
        self.api_key_entry.configure(show="*" if not self.show_key else "")
        self.toggle_btn.configure(text="👁" if not self.show_key else "🔒")

    def start_session(self):
        # API anahtarını al ve kontrol et
        user_key = self.api_key_entry.get().strip().replace('"', '').replace("'", "")
        if not user_key:
            messagebox.showerror("Hata", "Lütfen geçerli bir Gemini API Anahtarı girin.")
            return

        try:
            local_client = genai.Client(
                http_options={"api_version": "v1beta"},
                api_key=user_key,
            )
        except Exception as e:
            messagebox.showerror("Hata", f"Gemini Client başlatılamadı: {e}")
            return

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.api_key_entry.configure(state="disabled")
        self.toggle_btn.configure(state="disabled")
        
        self.log("\n--- Yeni Oturum (Türkçe ➔ İngilizce) ---\n")

        self.audio_loop = LiveAudioTranslator(client=local_client, gui_queue=self.gui_queue)
        self.loop_thread = threading.Thread(target=self.run_async_loop, daemon=True)
        self.loop_thread.start()

    def run_async_loop(self):
        asyncio.run(self.audio_loop.run())

    def stop_session(self):
        if self.audio_loop:
            self.audio_loop.stop()
        self.stop_btn.configure(state="disabled")
        self.target_volume = 0.0

    def on_closing(self):
        """Pencere kapatıldığında ses dosyalarının bozulmaması için güvenli çıkış yapar."""
        self.stop_session()
        if self.loop_thread and self.loop_thread.is_alive():
            self.loop_thread.join(timeout=2.0)
        self.root.destroy()

    def poll_queue(self):
        while True:
            try:
                msg = self.gui_queue.get_nowait()
            except queue.Empty:
                break

            if msg["type"] == "status":
                self.status_lbl.configure(text=f"Durum: {msg['val']}")
            elif msg["type"] == "transcript":
                self.log(msg["val"])
            elif msg["type"] == "volume":
                # RMS değerini normalize edip hedef sese ata (Multiplier ile hassasiyet ayarı)
                self.target_volume = min(1.0, msg["val"] * 12.0)
                if self.animation_idle:
                    self.animation_idle = False
                    self.update_animation()
            elif msg["type"] == "error":
                self.log(f"\nHata Oluştu: {msg['val']}\n")
                self.stop_session()
            elif msg["type"] == "terminated":
                self.start_btn.configure(state="normal")
                self.api_key_entry.configure(state="normal")
                self.toggle_btn.configure(state="normal")
                self.audio_loop = None
                self.loop_thread = None
                
        self.root.after(50, self.poll_queue)


if __name__ == "__main__":
    root = tk.Tk()
    app = TranslatorGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
