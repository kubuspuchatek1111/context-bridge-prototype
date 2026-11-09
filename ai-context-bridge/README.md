# ai-context-bridge

Małe CLI dla Pythona 3.11+, które zapisuje checkpoint wznowienia pracy w jawnym repozytorium Git. Git dostarcza wszystkie fakty. Model dostarcza jedynie propozycję następnego kroku i krótkie wnioski z etykietą pewności.

## Instalacja

Wymagane są Python 3.11+ i Git. Z katalogu projektu:

```sh
python -m pip install .
```

Testy:

```sh
python -m pip install pytest
python -m pytest
```

## Użycie

```sh
bridge pause --repo /ścieżka/do/repo [--dry-run] [--lang pl|en]
bridge resume --repo /ścieżka/do/repo [--lang pl|en]
```

`--repo` można pominąć, wtedy używany jest bieżący katalog. Domyślny język to `pl`.

`pause` zbiera branch, do 10 ostatnich commitów (pełne hashe), stan plików ze ścieżkami i listę stashy. Przy pełnej konfiguracji LLM proponuje jednozdaniowy następny krok oraz wnioski oznaczone `low`, `medium` albo `high`. Możesz zatwierdzić szkic Enterem albo wpisać własny krok w odpowiedzi na pytanie „Co jest następnym krokiem?”. Niepoprawna odpowiedź modelu jest ponawiana raz, a potem checkpoint zawiera tylko fakty. Bez pełnej konfiguracji zmiennych środowiskowych program jasno informuje o pracy w trybie tylko faktów.

```sh
export BRIDGE_BASE_URL="https://api.example.com/v1"
export BRIDGE_API_KEY="..."
export BRIDGE_MODEL="model-name"
```

Endpoint musi obsługiwać Chat Completions zgodne z OpenAI. Lokalny serwer, np. Ollama, może być użyty przez ustawienie jego adresu `BRIDGE_BASE_URL` i dowolnej wymaganej przez niego wartości `BRIDGE_API_KEY`.

`pause --dry-run` drukuje JSON żądania, w tym model i wiadomości, bez połączenia z siecią ani zapisywania checkpointu. Do modelu trafiają wyłącznie nazwy zmienionych plików i komunikaty commitów. Nie trafiają tam hashe, statusy, dane stashy, treści plików ani diffy. `resume` nie wywołuje modelu; pokazuje checkpoint, nowe commity, zmienione ścieżki i ostrzeżenie, jeśli branch jest inny.

Zapis odbywa się do `.bridge/checkpoint.md`. CLI wypisuje pełną ścieżkę zapisywanego pliku. Nie zmienia `.gitignore` ani konfiguracji wykluczeń Git.

## Co opuszcza Twój komputer

Przy skonfigurowanym providerze poza komputer wysyłane są tylko nazwy zmienionych plików oraz tematy do 10 ostatnich commitów, do wskazanego przez `BRIDGE_BASE_URL` endpointu. Żadne treści plików, diffy, hashe commitów, branch, statusy Git, stash ani zapisany checkpoint nie są wysyłane. Bez kompletu `BRIDGE_BASE_URL`, `BRIDGE_API_KEY` i `BRIDGE_MODEL` połączenie nie jest wykonywane. `--dry-run` nie wykonuje połączeń niezależnie od konfiguracji.
