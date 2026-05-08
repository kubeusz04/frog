# Frog na Oracle Cloud VPS

Repo:

```text
https://github.com/kubeusz04/frog
```

## 1. Polacz sie przez SSH

Na Ubuntu obraz zwykle uzytkownik to:

```powershell
ssh -i .\klucz.key ubuntu@138.2.148.228
```

Jesli masz obraz Oracle Linux, uzyj:

```powershell
ssh -i .\klucz.key opc@138.2.148.228
```

## 2. Sklonuj repo

Na VPS:

```bash
git clone https://github.com/kubeusz04/frog.git
cd frog
```

## 3. Dodaj cookies YouTube

Wklej odfiltrowane cookies:

```bash
nano youtube-cookies.txt
```

Wklej zawartosc lokalnego `youtube-cookies.txt`, zapisz `Ctrl+O`, Enter, wyjdz `Ctrl+X`.

Ten plik nie powinien isc na GitHub.

## 4. Uruchom backend

```bash
chmod +x oracle_setup.sh
./oracle_setup.sh
```

Sprawdz:

```bash
curl http://localhost:8000/api/health
```

Z komputera/telefonu:

```text
http://138.2.148.228:8000/api/health
```

## 5. Oracle firewall

W panelu Oracle musisz miec ingress rule:

```text
Source CIDR: 0.0.0.0/0
IP Protocol: TCP
Destination Port Range: 8000
```

Jesli port 8000 nie odpowiada z zewnatrz, problem prawie zawsze jest w Security List / Network Security Group w Oracle.

## 6. Android

Po uruchomieniu VPS zmien w Androidzie:

```kotlin
const val SERVER_URL = "https://frog-mobile-api.onrender.com"
```

na:

```kotlin
const val SERVER_URL = "http://138.2.148.228:8000"
```

Potem zbuduj APK ponownie:

```bat
cd C:\Users\Zawadzki\Desktop\jamik\android\FrogAndroid
gradlew.bat assembleDebug
```
