# Jak ominąć błąd 403 Forbidden (Cloudflare WAF) w FanFilm

Wiele angielskich źródeł (np. **VidLink** korzystający z serwerów proxy MegaCloud na domenie `storm.vodvidl.site`) chronionych jest przez zaporę Cloudflare. Powoduje to błędy `403 Forbidden` podczas próby odtwarzania w zewnętrznych odtwarzaczach, takich jak `mpv`.

Aby to naprawić, możemy zautomatyzować proces przy użyciu rozszerzenia **Tampermonkey** lub zrobić to ręcznie.

---

## Metoda 1: Automatyczna przez Tampermonkey (Rekomendowana)

W projekcie znajduje się skrypt użytkownika `fanfilm.user.js`, który automatycznie wykrywa ciasteczka `cf_clearance` podczas przeglądania stron i wysyła je bezpośrednio do uruchomionego serwera FanFilm TUI (na porcie `8663`), bez potrzeby ręcznego kopiowania!

### Krok 1: Instalacja Tampermonkey i Skryptu
1. Zainstaluj rozszerzenie **Tampermonkey** w swojej przeglądarce.
2. Wejdź w ustawienia Tampermonkey, zmień tryb konfiguracji na **Zaawansowany (Expert)**.
3. W sekcji **Bezpieczeństwo (Security)** znajdź opcję **Dostęp do ciasteczek skryptów (Allow scripts to access cookies)**, zmień jej wartość na **Wszystkie (All)** i kliknij **Zapisz (Save)**.
4. Zainstaluj skrypt z lokalnego serwera FanFilm (otwórz w przeglądarce adres `http://localhost:8663/mod/fanfilm.user.js` lub zaimportuj plik [fanfilm.user.js](file:///home/voidy/rzeczy/repo/fanfilm/plugin.video/plugin.video.fanfilm/web/mod/fanfilm.user.js) do Tampermonkey).

### Krok 2: Automatyczna synchronizacja
1. Upewnij się, że FanFilm TUI jest uruchomiony (`./run_tui.sh`).
2. Otwórz w przeglądarce stronę [https://vidlink.pro](https://vidlink.pro) i odtwórz dowolny film na chwilę.
3. Tampermonkey automatycznie przechwyci ciasteczka Cloudflare oraz Twój User-Agent, a następnie wyśle je bezpośrednio do TUI. Na ekranie w przeglądarce zobaczysz mały zielony panel z komunikatem `✅ Wysłano`, a w TUI pojawi się powiadomienie `Otrzymano ciasteczka`.

---

## Metoda 2: Ręczna (Alternatywna)

## Krok 1: Przygotowanie przeglądarki

Do łatwego pobrania ciasteczek zainstaluj w przeglądarce (Chrome, Firefox, Brave itp.) dowolne rozszerzenie do zarządzania ciasteczkami. Bardzo dobrze sprawdza się:
*   **Cookie-Editor** (dostępny w Chrome Web Store / Firefox Add-ons)
*   **Copy Cookies**

---

## Krok 2: Wygenerowanie ciasteczka w przeglądarce

1. Otwórz przeglądarkę i wejdź na stronę: [https://vidlink.pro](https://vidlink.pro).
2. Wyszukaj i uruchom dowolny film/serial, tak aby player załadował się i wideo zaczęło się odtwarzać.
   * *Dlaczego to robimy?* W tle player wyśle żądanie do serwerów proxy (np. `storm.vodvidl.site`), co automatycznie rozwiąże wyzwanie Cloudflare i zapisze ciasteczko autoryzacyjne w przeglądarce.
3. Kliknij ikonę wtyczki **Cookie-Editor** i upewnij się, że jesteś na domenie powiązanej z odtwarzaczem strumienia (np. `storm.vodvidl.site` lub `megacloud.live`).
4. Znajdź ciasteczko o nazwie **`cf_clearance`** i skopiuj jego wartość (długi ciąg liter i cyfr). Alternatywnie możesz wyeksportować całe ciasteczka w formacie JSON – FanFilm automatycznie wyodrębni z nich to właściwe.

---

## Krok 3: Pobranie nagłówka User-Agent

Cloudflare wymaga, aby zapytania z odtwarzacza `mpv` posiadały dokładnie taki sam nagłówek `User-Agent` jak przeglądarka, z której skopiowano ciasteczko.

1. Otwórz nową kartę w przeglądarce.
2. Wpisz w wyszukiwarkę Google frazę: **`my user agent`** lub wejdź na stronę [whatsmyua.info](https://whatsmyua.info).
3. Skopiuj wyświetlony ciąg (np. `Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...`).

---

## Krok 4: Konfiguracja w FanFilm TUI

1. Uruchom odtwarzacz FanFilm TUI w terminalu:
   ```bash
   ./run_tui.sh
   ```
2. Wejdź w **Ustawienia** (`Settings`).
3. Przejdź do sekcji **Źródła (angielskie)** i znajdź pozycję **VidLink**.
4. Wklej skopiowane dane do odpowiednich pól:
   * **Ciasteczka Cloudflare (cf_clearance)**: wklej skopiowaną wartość ciasteczka `cf_clearance` (lub wyeksportowany JSON).
   * **User Agent**: wklej skopiowany ciąg User-Agent.
5. Zapisz ustawienia.

Od tej pory odtwarzacz `mpv` będzie automatycznie autoryzował się w Cloudflare przy użyciu Twoich danych sesyjnych, co pozwoli na bezproblemowe odtwarzanie filmów ze źródła VidLink.

> [!NOTE]
> Ciasteczko `cf_clearance` ma określony czas ważności (zazwyczaj od kilku tygodni do kilku miesięcy). Jeśli po pewnym czasie błąd 403 powróci, wystarczy powtórzyć powyższe kroki w celu wygenerowania nowego ciasteczka.
