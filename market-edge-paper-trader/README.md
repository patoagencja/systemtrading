# Market Edge Paper Trader

System do **wirtualnego tradingu** (paper trading) na kapitale 1 000 000 PLN.
Skanuje rynek, generuje sygnały z 4 strategii technicznych, otwiera wirtualne pozycje i mierzy wyniki.

**Nie wykonuje żadnych prawdziwych transakcji. Żadne pieniądze nie są zaangażowane.**

---

## 🌐 URUCHOMIENIE ONLINE (bez instalowania niczego) — ZALECANE dla nietechnicznych

Możesz uruchomić cały system **w chmurze, za darmo, klikając tylko w przeglądarce**.
Nie musisz instalować Pythona ani niczego pobierać.

### Część A — Dashboard online (Streamlit Cloud)

1. Wejdź na **[share.streamlit.io](https://share.streamlit.io)**
2. Kliknij **Sign in** i zaloguj się swoim kontem **GitHub** (tym, które ma dostęp do `patoagencja/systemtrading`)
3. Kliknij **Create app** → **Deploy a public app from GitHub** (lub „from existing repo")
4. Wypełnij pola:
   - **Repository:** `patoagencja/systemtrading`
   - **Branch:** `claude/laughing-lovelace-gtvcia`
   - **Main file path:** `market-edge-paper-trader/dashboard/streamlit_app.py`
5. Kliknij **Deploy**
6. Po 1–2 minutach dostaniesz **adres URL** (np. `https://twojanazwa.streamlit.app`) — to jest Twój dashboard. Zapisz go w zakładkach.

### Część B — Uruchamianie systemu (przyciski w dashboardzie)

W dashboardzie po lewej stronie (panel boczny) masz przyciski:

- **🔄 Uruchom skan** — pobiera najnowsze dane, otwiera/zamyka pozycje (1–3 min)
- **📊 Uruchom backtest** — symuluje ostatnie 12 miesięcy i od razu pokazuje wyniki (3–8 min)
- **♻️ Reset portfela** — czyści wszystko i zaczyna od zera

> **Na start kliknij „Uruchom backtest"** — po kilku minutach zobaczysz pełny dashboard
> z prawdziwymi danymi: krzywą kapitału, transakcjami i statystykami.

⚠️ **Ważne:** dane wpisane przyciskami w dashboardzie utrzymują się tylko w trakcie sesji.
Jeśli aplikacja „uśnie" (po dłuższej nieaktywności), dane wrócą do ostatniego zapisanego stanu.
Aby system **codziennie sam handlował i zapisywał wyniki trwale** przez 1–2 miesiące — włącz robota (Część C).

### Część C — Automatyczny robot (GitHub Actions) — dla trwałego testu 1–2 miesiące

System ma wbudowanego „robota", który codziennie sam skanuje rynek i zapisuje wyniki:

1. Wejdź na **github.com/patoagencja/systemtrading**
2. Kliknij zakładkę **Actions** (u góry)
3. Jeśli zobaczysz przycisk „I understand my workflows, enable them" — kliknij go
4. Z lewej listy wybierz **Run 12-Month Backtest** → kliknij **Run workflow** → wybierz gałąź `claude/laughing-lovelace-gtvcia` → **Run workflow**.
   Po kilku minutach wyniki zapiszą się i pojawią w dashboardzie (kliknij „🔄 Refresh data").
5. Aby uruchomić pojedynczy skan ręcznie: wybierz **Daily Paper Trading Scan** → **Run workflow**.

**Automatyczne codzienne skany** (bez Twojego udziału) działają tylko, gdy te pliki są
na **głównej gałęzi (main)** repozytorium. Jeśli chcesz włączyć pełną automatykę 24/7,
poproś o połączenie tej gałęzi z `main` (mogę przygotować to na życzenie).

> **Uwaga:** w ustawieniach repozytorium musi być włączony zapis dla Actions:
> *Settings → Actions → General → Workflow permissions → „Read and write permissions"*.

---

## 💻 Szybki start LOKALNY (na własnym komputerze, dla zaawansowanych)

### 1. Zainstaluj Pythona

Wejdź na [python.org/downloads](https://www.python.org/downloads/) i pobierz Python **3.11** lub nowszy.
Podczas instalacji zaznacz opcję **"Add Python to PATH"**.

Sprawdź instalację — otwórz terminal (CMD lub PowerShell na Windows, Terminal na Mac/Linux) i wpisz:

```
python --version
```

Powinno pokazać `Python 3.11.x` lub wyżej.

---

### 2. Pobierz projekt i wejdź do folderu

```
cd market-edge-paper-trader
```

---

### 3. Utwórz wirtualne środowisko

```
python -m venv venv
```

Aktywuj je:

- **Windows:** `venv\Scripts\activate`
- **Mac/Linux:** `source venv/bin/activate`

Po aktywacji zobaczysz `(venv)` na początku linii terminala.

---

### 4. Zainstaluj wymagane pakiety

```
pip install -r requirements.txt
```

Pobierze wszystkie potrzebne biblioteki (~2–3 minuty).

---

### 5. Zainicjalizuj bazę danych

```
python scripts/init_db.py
```

Tworzy plik `data/database.sqlite` ze wszystkimi tabelami.

---

### 6. Załaduj listę tickerów

```
python scripts/seed_watchlist.py
```

Ładuje 85+ tickerów z pliku `data/watchlist.csv` do bazy.

---

### 7. Zresetuj portfel (opcjonalnie, przed świeżym startem)

```
python scripts/reset_portfolio.py
```

Kasuje wszystkie poprzednie transakcje i sygnały. Potwierdź wpisując `YES`.

---

### 8. Uruchom backtest (ostatnie 12 miesięcy)

```
python scripts/run_backtest.py
```

- Symuluje handel dzień po dniu przez ostatnie 12 miesięcy
- Nie używa danych z przyszłości
- Zapisuje wszystkie transakcje do bazy
- Na końcu pokazuje krzywą kapitału i statystyki

**Uwaga:** pierwsze uruchomienie pobiera dane dla 85+ tickerów — może trwać 5–15 minut.

---

### 9. Uruchom live paper scan (codziennie)

```
python scripts/run_scan.py
```

- Pobiera najnowsze dane rynkowe
- Aktualizuje otwarte pozycje (sprawdza stop lossy, take profity, max holding)
- Szuka nowych sygnałów
- Otwiera wirtualne pozycje
- Zapisuje snapshot portfela

Uruchamiaj raz dziennie po zamknięciu rynku USA (po 22:00 czasu polskiego).

---

### 10. Uruchom dashboard

```
streamlit run dashboard/streamlit_app.py
```

Otworzy się przeglądarka z adresem `http://localhost:8501`.
Pokaże kapitał, pozycje, wyniki, wykresy i statystyki.

---

## Jak dodać własne tickery

Otwórz plik `data/watchlist.csv` w Excelu lub Notatniku.

Format każdej linii:
```
TICKER,sektor,czy_etf(0/1),etf_sektora
```

Przykład:
```
NVDA,semi,0,SOXX
SPY,broad,1,
```

Po dodaniu uruchom:
```
python scripts/seed_watchlist.py
```

---

## Jak zmienić ryzyko i parametry

Otwórz plik `.env` w Notatniku i zmień wartości:

| Parametr | Opis | Domyślnie |
|---|---|---|
| `INITIAL_CAPITAL_PLN` | Kapitał startowy | 1 000 000 |
| `RISK_PER_TRADE_PCT` | Ryzyko na transakcję (% portfela) | 0.005 = 0.5% |
| `MAX_POSITION_SIZE_PCT` | Max rozmiar pozycji (% portfela) | 0.03 = 3% |
| `MAX_OPEN_POSITIONS` | Maks. liczba otwartych pozycji | 40 |
| `MAX_PORTFOLIO_EXPOSURE_PCT` | Maks. zaangażowanie portfela | 0.80 = 80% |
| `MIN_SCORE_TO_OPEN` | Min. score do otwarcia pozycji | 75 |
| `MAX_HOLDING_DAYS` | Domyślny maks. czas trzymania | 10 |
| `PLN_USD_RATE` | Kurs USD/PLN | 4.00 |

---

## Jak interpretować wyniki

### Dashboard — główne wskaźniki:

- **Total Return %** — zwrot od startu. Pozytywny = zarabiamy, negatywny = tracimy.
- **Win Rate** — % wygranych transakcji. Dobry system: 40–60%.
- **Profit Factor** — suma wygranych ÷ suma strat. Powyżej 1.5 to dobry wynik.
- **Avg R-Multiple** — średnia wielokrotność ryzyka na transakcję. Powyżej 0.5 to dobry wynik.
- **Max Drawdown** — największy spadek kapitału od szczytu. Powyżej 20% to duże ryzyko.

### Equity Curve (krzywa kapitału):

- Rosnąca linia = system zarabia
- Płaska linia = system nie traci, ale też nie zarabia
- Opadająca linia = system traci

### Ważna zasada:

**Po 1 miesiącu lub 20 transakcjach wyniki są statystycznie bez znaczenia.**
Minimum 100–200 zamkniętych transakcji daje wiarygodny obraz skuteczności.

---

## Strategie

System używa 4 strategii technicznych:

1. **MEAN_REVERSION_UPTREND** — kupuje po cofnięciu w trendzie wzrostowym (RSI < 35)
2. **MOMENTUM_BREAKOUT** — kupuje wybicia powyżej 20-dniowego maksimum z wolumenem
3. **PULLBACK_TREND** — kupuje cofnięcia do SMA50 w trendzie wzrostowym
4. **ETF_RELATIVE_STRENGTH** — kupuje akcje silniejsze niż SPY w ciągu 20 dni

Każda pozycja ma:
- **Stop Loss** = cena wejścia − 2× ATR
- **Take Profit** = cena wejścia + 1.5×R lub 2×R (zależnie od strategii)

---

## Ważne zastrzeżenia

- **To nie jest gwarancja zysku.** Wyniki historyczne i symulacje nie gwarantują przyszłych zysków.
- **yfinance ma ograniczenia** — dane mogą być opóźnione, niekompletne lub zawierać błędy. Nie używaj ich do realnego tradingu.
- **Brak prowizji maklerskich** — w realu każda transakcja kosztuje (np. 5–15 USD na Interactive Brokers).
- **Brak slippage** — w realu cena realizacji może się różnić od ceny sygnału, zwłaszcza na małych spółkach.
- **Brak podatków** — w Polsce zyski z akcji są opodatkowane 19% (podatek Belki).
- **Uproszczony kurs USD/PLN** — system używa stałego kursu z `.env`. W realu kurs się zmienia co sekundę.
- **Brak filtra wynikowego** — system może kupować przed publikacją wyników kwartalnych, co jest ryzykowne.
- **1 miesiąc nic nie udowadnia** — potrzebujesz minimum 100–200 transakcji, żeby ocenić system.
- **Najważniejszy jest wynik po czasie** — uruchom system przez 2–3 miesiące, a potem oceń.

---

## Struktura projektu

```
market-edge-paper-trader/
├── app/
│   ├── config.py          # Konfiguracja (wczytuje .env)
│   ├── database.py        # SQLite, inicjalizacja tabel
│   ├── data_provider.py   # Pobieranie danych z yfinance
│   ├── indicators.py      # Wskaźniki techniczne (SMA, RSI, ATR...)
│   ├── strategies.py      # 4 strategie tradingowe
│   ├── scoring.py         # Ocena sygnałów 0–100
│   ├── risk_manager.py    # Zarządzanie ryzykiem, sizing pozycji
│   ├── portfolio.py       # Stan portfela, snapshots
│   ├── paper_broker.py    # Wirtualny broker (otwieranie/zamykanie pozycji)
│   ├── scanner.py         # Główna logika skanowania rynku
│   ├── reporting.py       # Raport terminalowy
│   └── utils.py           # Narzędzia pomocnicze
├── dashboard/
│   └── streamlit_app.py   # Interfejs webowy
├── scripts/
│   ├── init_db.py         # Inicjalizacja bazy
│   ├── seed_watchlist.py  # Ładowanie tickerów
│   ├── reset_portfolio.py # Reset danych
│   ├── run_scan.py        # Live paper scan
│   └── run_backtest.py    # Backtest 12 miesięcy
├── data/
│   ├── watchlist.csv      # Lista tickerów
│   └── database.sqlite    # Baza danych (tworzona automatycznie)
├── tests/                 # Testy automatyczne
├── .env                   # Konfiguracja (kurs, ryzyko, limity)
└── requirements.txt       # Wymagane biblioteki
```

---

## Uruchamianie testów

```
pytest tests/ -v
```

---

*Wersja: MVP 1.0 | Tylko do celów edukacyjnych i symulacyjnych.*
