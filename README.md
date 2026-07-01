# Gemini Live Audio Translator

Bu uygulama, bilgisayarınızın mikrofonundan gelen canlı Türkçe sesi arka planda sessizce dinleyerek, Google'ın yeni nesil **Gemini Live API**'sini (gemini-3.5-live-translate-preview) kullanarak anında İngilizceye çevirir. 

Uygulama %100 "Clean Code" prensiplerine sadık kalınarak, boştayken işlemci (CPU) ve bellek (RAM) tüketimi %0 olacak şekilde maksimum optimizasyonla geliştirilmiştir.

## Özellikler

- **Canlı ve Sessiz Çeviri:** Sesleri arayüze yansıtmadan doğrudan hedefe çevirir.
- **Otomatik Kayıt:** Her oturum için ses dosyalarını (WAV) ve metin çevirilerini (TXT) otomatik olarak kaydeder.
- **Sıfır Donanım Yükü:** Ses yokken animasyonlar ve döngüler durdurularak donanım optimizasyonu sağlanır.
- **Temiz Arayüz:** Modern, karanlık temalı Tkinter arayüzü ile kullanımı oldukça basittir.

## Kurulum

1. Bilgisayarınızda Python yüklü olduğundan emin olun.
2. Gerekli kütüphaneleri kurmak için terminal veya komut satırında şu komutu çalıştırın:
   ```bash
   pip install google-genai pyaudio
   ```

## Kullanım

Uygulamayı başlatmak için terminalden şu komutu girin:
```bash
py gemini-live.py
```

1. Program açıldığında Gemini API Anahtarınızı girin.
2. **BAŞLAT** butonuna tıklayarak mikrofonunuzdan konuşmaya başlayın.
3. Oturumu bitirmek istediğinizde **DURDUR** butonuna tıklayın. 
4. Uygulama otomatik olarak çeviriyi ve sesi proje klasörüne kaydedecektir.

## Güvenlik
- Girdiğiniz API anahtarı hiçbir şekilde kaynak koda veya başka bir yere kaydedilmez, her oturumda güvenli bir şekilde sadece bellekte (in-memory) tutulur.
- Çevre değişkenlerinde (Environment Variables) `GEMINI_API_KEY` tanımlıysa uygulama bunu otomatik olarak tanır.

## Hakkında
- **Web:** [www.farukguler.com](http://www.farukguler.com)
- **GitHub:** [github.com/faruk-guler](https://github.com/faruk-guler)
