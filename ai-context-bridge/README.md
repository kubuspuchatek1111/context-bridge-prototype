# ai-context-bridge

Minimalne, lokalne CLI do zapisywania checkpointu wznowienia pracy w jawnym repozytorium git. Program nie używa AI/LLM, sieci ani zależności runtime poza biblioteką standardową Pythona.

## Instalacja

Wymagany jest Python 3.9+ oraz git. Z katalogu projektu:

```sh
python -m pip install .
```

Testy wymagają pytest:

```sh
python -m pip install pytest
python -m pytest
```

## Użycie

```sh
bridge pause --repo /ścieżka/do/repo
bridge resume --repo /ścieżka/do/repo
```

`--repo` można pominąć, aby użyć bieżącego katalogu. Język nagłówków i komunikatów wybiera `--lang pl` (domyślnie) albo `--lang en`; opcję można podać przed komendą, np. `bridge --lang en pause --repo .`.

`pause` zapisuje `.bridge/checkpoint.md` po jednym pytaniu o następny krok. Zapis zawiera branch, do 10 ostatnich commitów, stan plików, statystyki `git diff --stat`, listę stashy i notatkę z czasem. Statystyki nie zawierają treści diffu. `resume` pokazuje checkpoint, nowe commity, zmienione pliki oraz ostrzega o zmianie brancha.

Po zapisie CLI wypisuje pełną ścieżkę pliku oraz podpowiedź, by dodać `.bridge/` do `.git/info/exclude`. Narzędzie samo nie modyfikuje `.gitignore` ani plików exclude.
