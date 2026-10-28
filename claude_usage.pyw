#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Token Usage - kleines Desktop-Fenster mit den Daten aus /usage.

Zeigt:
  * Auslastung der Nutzungslimits (5-Stunden-Fenster und Wochenfenster) in Prozent,
    inklusive Countdown bis zum jeweiligen Reset. Quelle: OAuth-Endpunkt von Anthropic.
  * Beim Wochenlimit zusätzlich ein Raster der sieben Tage und eine Soll-Marke:
    der zeitlich anteilige Verbrauch (100 %/7 je Tag), der gleichmäßig bis zum
    Reset reicht, samt Abstand dazu in Prozentpunkten.
  * Tatsächlich verarbeitete Tokens je Modell, ausgezählt aus den lokalen
    Transcript-Dateien (~/.claude/projects/**/*.jsonl), gefiltert auf genau die
    Zeitfenster der beiden Limits. Gezählt werden Eingabe, Ausgabe und das
    Anlegen des Caches; die Cache-Lesevorgänge stehen separat, weil sie
    denselben Kontext immer wieder lesen und die Summe sonst beherrschen.

Der Zugriffstoken wird aus der Anmeldedatei von Claude Code gelesen und
ausschließlich an api.anthropic.com gesendet.

Die Oberfläche spricht Deutsch oder Englisch, umschaltbar über DE/EN in der
Kopfzeile; die Texte stehen in TEXTE, die Wahl wird in einstellungen.json
gemerkt.
"""

import json
import os
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from tkinter import ttk

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
AKTUALISIERUNG_SEK = 300         # Abstand zwischen zwei Abrufen
# Wartezeiten nach einer Drosselung (HTTP 429), je weiterem Fehlversuch länger.
# Auf den Endpunkt greifen auch Claude Code selbst und die Apps zu; zu häufiges
# Abfragen wird gemeinsam gezählt.
WARTE_NACH_DROSSELUNG = (300, 600, 1200, 1800)
# Dazwischen werden nur die lokalen Transcripts neu gezählt (kein Netzzugriff),
# um den Prozentwert bis zum nächsten echten Abruf fortzuschreiben.
LOKAL_TAKT_SEK = 30
FENSTER_TITEL = "Claude Token Usage"
EINSTELLUNGSDATEI = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "einstellungen.json")
# Standardgrößen der beiden Ansichten
GROESSE_VOLL = (430, 560)
GROESSE_KOMPAKT = (360, 150)

# ---------------------------------------------------------------- Farbschema
FARBEN = {
	"bg":        "#1b1b1f",
	"karte":     "#25252b",
	"text":      "#e8e6e3",
	"text_grau": "#9a978f",
	"spur":      "#3a3a42",
	"gruen":     "#4caf7d",
	"gelb":      "#d8a33a",
	"rot":       "#d4574a",
	"akzent":    "#c07a52",
}


def balkenfarbe(prozent):
	"""Ampelfarbe: grün bis 50 %, gelb bis 80 %, darüber rot."""
	if prozent >= 80:
		return FARBEN["rot"]
	if prozent >= 50:
		return FARBEN["gelb"]
	return FARBEN["gruen"]


# ----------------------------------------------------------------- Sprachen
SPRACHEN = ("de", "en")
SPRACHE = "de"                   # gewählte Oberflächensprache, umschaltbar

# Je Schlüssel die deutsche und die englische Fassung, in dieser Reihenfolge.
TEXTE = {
	# Schalter und Status
	"kompakt":          ("Kompakt", "Compact"),
	"vordergrund":      ("Vordergrund", "On top"),
	"aktualisieren":    ("Aktualisieren", "Refresh"),
	"laden":            ("lade …", "loading …"),
	"stand":            ("Stand %s", "Updated %s"),
	# Kartenüberschriften
	"karte_limits":     ("NUTZUNGSLIMITS", "USAGE LIMITS"),
	"karte_verteilung": ("WOCHE NACH ANWENDUNG", "WEEK BY APP"),
	"karte_tokens":     ("VERARBEITETE TOKENS (lokale Transcripts)",
	                     "TOKENS PROCESSED (local transcripts)"),
	# Beschriftungen der Limits
	"limit_session":    ("Sitzung (5 Stunden)", "Session (5 hours)"),
	"limit_weekly":     ("Woche (alle Modelle)", "Week (all models)"),
	"limit_opus":       ("Woche (Opus)", "Week (Opus)"),
	"limit_sonnet":     ("Woche (Sonnet)", "Week (Sonnet)"),
	"wahl_session":     ("Sitzung", "Session"),
	"wahl_weekly":      ("Woche", "Week"),
	# Balkenfuß
	"reset":            ("Reset %s (%s)", "Resets %s (%s)"),
	"reset_faellig":    ("Reset fällig", "reset due"),
	"lokal_berechnet":  ("≈ lokal berechnet", "≈ computed locally"),
	"rest_tage":        ("in %d Tg %d h", "in %dd %dh"),
	"rest_stunden":     ("in %d h %02d min", "in %dh %02dm"),
	"rest_minuten":     ("in %d min", "in %dm"),
	# Soll-Ist-Vergleich des Wochenfensters
	"plan_tag":         ("Tag %d/%d", "Day %d/%d"),
	"plan_soll":        ("Soll %d %%", "Target %d %%"),
	"plan_luft":        ("unter Soll", "under target"),
	"plan_genau":       ("im Soll", "on target"),
	"plan_drueber":     ("über Soll", "over target"),
	"plan_pro_tag":     ("noch %s %%/Tag", "%s %%/day left"),
	"plan_bis_reset":   ("noch %d %% bis Reset", "%d %% left until reset"),
	# Tokenaufstellung
	"keine_tokens":     ("keine Transcript-Daten im Zeitfenster",
	                     "no transcript data in this window"),
	"spalte_ein":       ("ein", "in"),
	"spalte_aus":       ("aus", "out"),
	"cache_lesen":      ("Cache-Lesen", "Cache reads"),
	"verarbeitet":      ("Verarbeitet", "Processed"),
	"rest_ca":          ("Rest ca.", "Est. left"),
	"keine_daten":      ("keine Daten", "no data"),
	"zusatzguthaben":   ("Zusatzguthaben: %d %% verbraucht", "Extra usage: %d %% used"),
	"kompakt_verbrauch": ("%s Tokens verarbeitet", "%s tokens processed"),
	"kompakt_rest":     ("noch ca. %s", "approx. %s left"),
	# Kurzformen großer Zahlen
	"anzahl_mrd":       ("%s Mrd", "%sB"),
	"anzahl_mio":       ("%s Mio", "%sM"),
	"anzahl_tsd":       ("%s Tsd", "%sK"),
	# Fehlermeldungen
	"fehler_anmeldung": ("Keine Anmeldedaten gefunden – ist Claude Code angemeldet?",
	                     "No credentials found – is Claude Code signed in?"),
	"fehler_token":     ("Zugriffstoken abgelaufen – Claude Code einmal starten.",
	                     "Access token expired – start Claude Code once."),
	"fehler_drosselung": ("Zu viele Abfragen (429)", "Too many requests (429)"),
	"fehler_http":      ("HTTP %s vom Usage-Endpunkt", "HTTP %s from the usage endpoint"),
	"fehler_erneut":    ("%s – neuer Versuch um %s", "%s – retrying at %s"),
	# Wochentagskürzel, Montag zuerst
	"wochentage":       ("Mo Di Mi Do Fr Sa So", "Mon Tue Wed Thu Fri Sat Sun"),
}


def t(schluessel, *args):
	"""Oberflächentext in der gewählten Sprache, optional mit %-Argumenten."""
	eintrag = TEXTE.get(schluessel)
	text = eintrag[SPRACHEN.index(SPRACHE)] if eintrag else schluessel
	return (text % args) if args else text


def setze_sprache(kuerzel):
	"""Sprache festlegen; unbekannte Angaben lassen die bisherige stehen."""
	global SPRACHE
	if kuerzel in SPRACHEN:
		SPRACHE = kuerzel
	return SPRACHE


def dezimal(text):
	"""Dezimaltrenner der Sprache: im Deutschen das Komma."""
	return text.replace(".", ",") if SPRACHE == "de" else text


# --------------------------------------------------------- Einstellungen
def lies_einstellungen():
	"""Gemerkte Ansicht, Fenstergrößen und Vordergrund-Schalter."""
	try:
		with open(EINSTELLUNGSDATEI, "r", encoding="utf-8") as fh:
			daten = json.load(fh)
		return daten if isinstance(daten, dict) else {}
	except (OSError, ValueError):
		return {}


def schreibe_einstellungen(daten):
	"""Einstellungen ablegen; scheitert das (z. B. schreibgeschützt), ist es egal."""
	try:
		with open(EINSTELLUNGSDATEI, "w", encoding="utf-8") as fh:
			json.dump(daten, fh, indent=1)
	except OSError:
		pass


# ------------------------------------------------------------ Dateizugriffe
def wsl_distributionen():
	"""Namen der installierten WSL-Distributionen (nur unter Windows)."""
	if os.name != "nt":
		return []
	try:
		import winreg
	except ImportError:
		return ["Ubuntu", "Debian"]
	namen = []
	try:
		with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
		                    r"Software\Microsoft\Windows\CurrentVersion\Lxss") as wurzel:
			index = 0
			while True:
				try:
					unterschluessel = winreg.EnumKey(wurzel, index)
				except OSError:
					break
				index += 1
				try:
					with winreg.OpenKey(wurzel, unterschluessel) as eintrag:
						name = winreg.QueryValueEx(eintrag, "DistributionName")[0]
				except OSError:
					continue
				# Hilfs-Distros von Container-Werkzeugen überspringen: Schon der
				# Dateizugriff startet sie (samt WSLg-Fenster, das den Fokus klaut).
				if name.lower().startswith(("podman-", "docker-desktop", "rancher-desktop")):
					continue
				namen.append(name)
	except OSError:
		return ["Ubuntu", "Debian"]
	return namen


def claude_verzeichnisse():
	"""Alle in Frage kommenden .claude-Verzeichnisse (Windows und WSL)."""
	kandidaten = []
	heim = os.path.expanduser("~")
	if heim:
		kandidaten.append(os.path.join(heim, ".claude"))

	# Unter Windows zusätzlich die Heimverzeichnisse in den WSL-Distributionen.
	if os.name == "nt":
		for distribution in wsl_distributionen():
			for wurzel in (r"\\wsl.localhost", r"\\wsl$"):
				heimat = os.path.join(wurzel, distribution, "home")
				try:
					benutzer = os.listdir(heimat)
				except OSError:
					continue
				for name in benutzer + ["../root"]:
					pfad = os.path.join(heimat, name, ".claude")
					if os.path.isdir(pfad):
						kandidaten.append(os.path.normpath(pfad))
				break     # Distribution erreichbar, zweite Schreibweise unnötig

	# Duplikate entfernen, Reihenfolge beibehalten
	gesehen, ergebnis = set(), []
	for pfad in kandidaten:
		schluessel = os.path.normcase(pfad)
		if schluessel not in gesehen and os.path.isdir(pfad):
			gesehen.add(schluessel)
			ergebnis.append(pfad)
	return ergebnis


def lies_zugriffstoken():
	"""Liefert den zuletzt erneuerten, noch gültigen Zugriffstoken."""
	beste = None
	for verzeichnis in claude_verzeichnisse():
		datei = os.path.join(verzeichnis, ".credentials.json")
		try:
			with open(datei, "r", encoding="utf-8") as fh:
				daten = json.load(fh)
		except (OSError, ValueError):
			continue
		oauth = daten.get("claudeAiOauth") or {}
		token = oauth.get("accessToken")
		if not token:
			continue
		# expiresAt ist ein Zeitstempel in Millisekunden
		laeuft_ab = oauth.get("expiresAt") or 0
		if beste is None or laeuft_ab > beste[0]:
			beste = (laeuft_ab, token)
	return beste[1] if beste else None


class KeineAnmeldung(Exception):
	"""Es liegen keine brauchbaren Anmeldedaten von Claude Code vor."""


def hole_limits(token):
	"""Ruft den Usage-Endpunkt ab und liefert das ausgewertete JSON."""
	anfrage = urllib.request.Request(
		USAGE_URL,
		headers={
			"Authorization": "Bearer " + token,
			"anthropic-beta": "oauth-2025-04-20",
			"User-Agent": "claude-token-usage/1.0",
			"Accept": "application/json",
		},
	)
	with urllib.request.urlopen(anfrage, timeout=20) as antwort:
		return json.loads(antwort.read().decode("utf-8"))


# ------------------------------------------------------- Tokens auszählen
def _zeitstempel(text):
	"""ISO-8601-Zeitstempel (auch mit 'Z') nach datetime mit Zeitzone."""
	if not text:
		return None
	try:
		return datetime.fromisoformat(text.replace("Z", "+00:00"))
	except ValueError:
		return None


def zaehle_tokens(fenster):
	"""
	Summiert die Tokens der Transcript-Dateien je Zeitfenster und Modell.

	fenster: dict {name: startzeitpunkt}
	Rückgabe: {name: {modell: {"input", "output", "cache_read", "cache_write"}}}
	"""
	frueheste = min(fenster.values())
	ergebnis = {name: {} for name in fenster}
	# Eine Message kann in mehreren Transcripts stehen (Fork, Kompaktierung),
	# darum je Zeitfenster über die Message-ID entduplizieren.
	gesehen = {name: set() for name in fenster}

	for verzeichnis in claude_verzeichnisse():
		projekte = os.path.join(verzeichnis, "projects")
		if not os.path.isdir(projekte):
			continue
		for wurzel, _, dateien in os.walk(projekte):
			for dateiname in dateien:
				if not dateiname.endswith(".jsonl"):
					continue
				pfad = os.path.join(wurzel, dateiname)
				try:
					# Dateien, die vor dem frühesten Fenster zuletzt geschrieben
					# wurden, können nichts Relevantes enthalten.
					if os.path.getmtime(pfad) < frueheste.timestamp():
						continue
					with open(pfad, "r", encoding="utf-8", errors="replace") as fh:
						for zeile in fh:
							if '"usage"' not in zeile:
								continue
							try:
								satz = json.loads(zeile)
							except ValueError:
								continue
							nachricht = satz.get("message")
							if not isinstance(nachricht, dict):
								continue
							verbrauch = nachricht.get("usage")
							if not isinstance(verbrauch, dict):
								continue
							zeitpunkt = _zeitstempel(satz.get("timestamp"))
							if zeitpunkt is None:
								continue
							modell = nachricht.get("model") or "unbekannt"
							if modell.startswith("<"):
								continue          # Platzhalter wie <synthetic>
							kennung = nachricht.get("id") or satz.get("uuid")
							for name, start in fenster.items():
								if zeitpunkt < start:
									continue
								if kennung is not None:
									if kennung in gesehen[name]:
										continue
									gesehen[name].add(kennung)
								eintrag = ergebnis[name].setdefault(
									modell,
									{"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
								)
								eintrag["input"] += verbrauch.get("input_tokens", 0) or 0
								eintrag["output"] += verbrauch.get("output_tokens", 0) or 0
								eintrag["cache_read"] += verbrauch.get("cache_read_input_tokens", 0) or 0
								eintrag["cache_write"] += verbrauch.get("cache_creation_input_tokens", 0) or 0
				except OSError:
					continue
	return ergebnis


# ---------------------------------------------------------- Hilfsfunktionen
def formatiere_anzahl(wert):
	"""Große Zahlen kurz darstellen: 1,2 Mio bzw. 1.2M."""
	for grenze, stellen, schluessel in ((1_000_000_000, 2, "anzahl_mrd"),
	                                    (1_000_000, 1, "anzahl_mio"),
	                                    (1_000, 1, "anzahl_tsd")):
		if wert >= grenze:
			return t(schluessel, dezimal("%.*f" % (stellen, wert / grenze)))
	return str(int(wert))


def formatiere_zeitpunkt(zeitpunkt):
	"""Reset-Zeitpunkt in Ortszeit mit dem Wochentagskürzel der Sprache."""
	ortszeit = zeitpunkt.astimezone()
	return "%s %s" % (t("wochentage").split()[ortszeit.weekday()],
	                  ortszeit.strftime("%H:%M"))


def formatiere_restzeit(ziel):
	"""Verbleibende Zeit bis zum Reset als 'in 2 h 35 min'."""
	if ziel is None:
		return ""
	rest = ziel - datetime.now(timezone.utc)
	sekunden = int(rest.total_seconds())
	if sekunden <= 0:
		return t("reset_faellig")
	tage, sekunden = divmod(sekunden, 86400)
	stunden, sekunden = divmod(sekunden, 3600)
	minuten = sekunden // 60
	if tage:
		return t("rest_tage", tage, stunden)
	if stunden:
		return t("rest_stunden", stunden, minuten)
	return t("rest_minuten", max(minuten, 1))


def summe_tokens(je_modell):
	"""
	Tokens eines Zeitfensters über alle Modelle aufsummieren.

	Ohne die Cache-Lesevorgänge: Sie lesen denselben Kontext bei jeder
	Nachricht erneut und wachsen daher mit der Länge einer Sitzung, ohne dass
	mehr Arbeit anfällt – in die Limits gehen sie nur zu einem Bruchteil ein.
	Gezählt wird also, was neu verarbeitet wurde: Eingabe, Ausgabe und das
	Anlegen des Caches. Die Lesevorgänge stehen in der Vollansicht separat.
	"""
	return sum(sum(werte.get(feld, 0) for feld in ("input", "output", "cache_write"))
	           for werte in je_modell.values())


def modellname(kennung):
	"""Technische Modell-ID auf einen lesbaren Namen kürzen."""
	name = kennung.replace("claude-", "")
	for alt, neu in (("opus-", "Opus "), ("sonnet-", "Sonnet "), ("haiku-", "Haiku "),
	                 ("fable-", "Fable ")):
		if name.startswith(alt):
			name = neu + name[len(alt):]
			break
	teile = name.split("-")
	# angehängtes Datum (z. B. -20251001) entfernen
	if len(teile) > 1 and teile[-1].isdigit() and len(teile[-1]) == 8:
		teile = teile[:-1]
	return "-".join(teile).replace("-", ".")


# ------------------------------------------------------------- Balkenwidget
class Limitbalken(tk.Frame):
	"""Eine Zeile: Beschriftung, Fortschrittsbalken, Prozent und Reset-Countdown."""

	def __init__(self, master, beschriftung, titel_font=None, wert_font=None,
	             fuss_font=None, hoehe=10, knapp=False, nur_balken=False, **kwargs):
		super().__init__(master, bg=FARBEN["karte"], **kwargs)
		self.reset_zeitpunkt = None
		self.hoehe = hoehe
		# knapp: ohne den Luftrand, den Labels von Haus aus mitbringen.
		# nur_balken: Beschriftung, Wert und Balken, sonst nichts – Countdown
		# und Soll-Ist-Vergleich stehen dann nur in der Vollansicht.
		self.nur_balken = nur_balken
		eng = {"pady": 0, "bd": 0} if knapp else {}

		kopf = tk.Frame(self, bg=FARBEN["karte"])
		kopf.pack(fill="x")
		self.titel = tk.Label(kopf, text=beschriftung, bg=FARBEN["karte"],
		                      fg=FARBEN["text"], anchor="w", font=titel_font, **eng)
		self.titel.pack(side="left")
		self.prozent = tk.Label(kopf, text="--", bg=FARBEN["karte"],
		                        fg=FARBEN["text"], anchor="e", font=wert_font, **eng)
		self.prozent.pack(side="right")

		self.balken = tk.Canvas(self, height=hoehe, bg=FARBEN["spur"],
		                        highlightthickness=0, bd=0)
		self.balken.pack(fill="x", pady=(3, 1) if knapp else (4, 2))
		self.fuellung = self.balken.create_rectangle(0, 0, 0, hoehe,
		                                             fill=FARBEN["gruen"], width=0)
		# Gerastertes Segment für den lokal fortgeschriebenen Zuwachs.
		self.fuellung_berechnet = self.balken.create_rectangle(0, 0, 0, hoehe,
		                                                       fill=FARBEN["gruen"],
		                                                       stipple="gray50", width=0)
		# Trennlinien der einzelnen Tage; werden bei Bedarf nachgelegt.
		self.raster_linien = []
		# Senkrechte Marke: der nach verstrichener Zeit anteilige Sollwert.
		# Liegt über den Füllungen, damit sie auch im gefüllten Teil sichtbar ist.
		self.marke_linie = self.balken.create_line(0, 0, 0, hoehe, fill=FARBEN["text"],
		                                           width=2, state="hidden")
		self.balken.bind("<Configure>", lambda _e: self._zeichne())

		self.fuss = tk.Label(self, text="", bg=FARBEN["karte"],
		                     fg=FARBEN["text_grau"], anchor="w", font=fuss_font,
		                     **eng)
		if not nur_balken:
			self.fuss.pack(fill="x")
		# Zweite Fußzeile, nur bei gesetzter Marke sichtbar: Abstand zum Plan.
		self.plan = tk.Label(self, text="", bg=FARBEN["karte"],
		                     fg=FARBEN["text_grau"], anchor="w", font=fuss_font,
		                     **eng)
		self._wert = 0.0
		self._berechnet = None
		self._marke = None
		self._raster = 0

	def setze_titel(self, beschriftung):
		"""Beschriftung austauschen – wird beim Sprachwechsel gebraucht."""
		self.titel.config(text=beschriftung)

	def setze(self, prozent, reset_zeitpunkt, berechnet=None, zusatz="", marke=None,
	          raster=0):
		"""prozent = vom Server bestätigt, berechnet = lokal fortgeschrieben."""
		self._wert = max(0.0, min(100.0, float(prozent or 0)))
		self.reset_zeitpunkt = reset_zeitpunkt
		text = "%d %%" % round(self._wert)
		self._berechnet = None
		if berechnet is not None:
			self._berechnet = max(self._wert, min(100.0, float(berechnet)))
			if round(self._berechnet) != round(self._wert):
				text += "  ·  ≈%d %%" % round(self._berechnet)
		self.prozent.config(text=text,
		                    fg=balkenfarbe(self._berechnet or self._wert))
		self._zusatz = zusatz
		self._marke = None if marke is None else max(0.0, min(100.0, float(marke)))
		self._raster = int(raster or 0)
		self._zeichne()
		self.aktualisiere_countdown()

	def setze_marke(self, marke):
		"""Sollwert allein nachziehen – er wandert mit der Uhr weiter."""
		self._marke = None if marke is None else max(0.0, min(100.0, float(marke)))
		self._zeichne()

	def aktualisiere_countdown(self):
		teile = []
		if self.reset_zeitpunkt is not None:
			teile.append(t("reset", formatiere_restzeit(self.reset_zeitpunkt),
			               formatiere_zeitpunkt(self.reset_zeitpunkt)))
		if self._berechnet is not None and round(self._berechnet) != round(self._wert):
			teile.append(t("lokal_berechnet"))
		if getattr(self, "_zusatz", ""):
			teile.append(self._zusatz)
		self.fuss.config(text="  ·  ".join(teile))
		self._zeige_plan()

	def _resttage(self):
		"""Verbleibende Tage des Fensters, oder None kurz vor dem Reset."""
		if self.reset_zeitpunkt is None:
			return None
		rest = (self.reset_zeitpunkt - datetime.now(timezone.utc)).total_seconds() / 86400.0
		return rest if rest > 0.05 else None

	def _zeige_plan(self):
		"""
		Soll-Ist-Vergleich: Das Soll ist der zeitlich anteilige Verbrauch – nach
		drei von sieben Tagen also 43 %. Es ist keine Grenze des Tarifs, sondern
		die Marke, die ein gleichmäßiger Verbrauch jetzt gerade erreicht hätte.
		Dazu, wie viel pro Tag noch bleibt, wenn der Rest gleichmäßig verteilt wird.
		"""
		if self._marke is None or self.nur_balken:
			if self.plan.winfo_manager():
				self.plan.pack_forget()
			return
		ist = self._wert if self._berechnet is None else self._berechnet
		abstand = ist - self._marke
		teile = []
		if self._raster:
			# Welcher Tag des Fensters gerade läuft – passend zum Raster im Balken.
			tag = min(self._raster, int(self._marke / 100.0 * self._raster) + 1)
			teile.append(t("plan_tag", tag, self._raster))
		teile.append(t("plan_soll", round(self._marke)))
		if abstand <= -1:
			teile.append(t("plan_luft"))
		elif abstand < 1:
			teile.append(t("plan_genau"))
		else:
			teile.append(t("plan_drueber"))
		tage = self._resttage()
		if tage and tage >= 1:
			teile.append(t("plan_pro_tag", dezimal("%.1f" % ((100.0 - ist) / tage))))
		elif tage:
			# Weniger als ein Tag übrig: ein Tagessatz sagt dann nichts mehr.
			teile.append(t("plan_bis_reset", round(100.0 - ist)))
		if abstand >= 10:
			farbe = FARBEN["rot"]
		elif abstand >= 2:
			farbe = FARBEN["gelb"]
		else:
			farbe = FARBEN["gruen"]
		self.plan.config(text="  ·  ".join(teile), fg=farbe)
		if not self.plan.winfo_manager():
			self.plan.pack(fill="x")

	def _zeichne_raster(self, breite):
		"""Senkrechte Linien an den Tagesgrenzen des Fensters."""
		anzahl = max(0, self._raster - 1)        # sieben Tage = sechs Grenzen
		while len(self.raster_linien) < anzahl:
			linie = self.balken.create_line(0, 0, 0, self.hoehe,
			                                fill=FARBEN["bg"], width=1)
			# über den Füllungen, aber unter der Soll-Marke
			self.balken.tag_lower(linie, self.marke_linie)
			self.raster_linien.append(linie)
		for nr, linie in enumerate(self.raster_linien, start=1):
			if nr > anzahl:
				self.balken.itemconfig(linie, state="hidden")
				continue
			x = breite * nr / float(self._raster)
			self.balken.coords(linie, x, 0, x, self.hoehe)
			self.balken.itemconfig(linie, state="normal")

	def _zeichne(self):
		breite = self.balken.winfo_width()
		bis_server = breite * self._wert / 100.0
		self.balken.coords(self.fuellung, 0, 0, bis_server, self.hoehe)
		self.balken.itemconfig(self.fuellung, fill=balkenfarbe(self._wert))
		if self._berechnet is not None and self._berechnet > self._wert:
			self.balken.coords(self.fuellung_berechnet, bis_server, 0,
			                   breite * self._berechnet / 100.0, self.hoehe)
			self.balken.itemconfig(self.fuellung_berechnet, state="normal",
			                       fill=balkenfarbe(self._berechnet))
		else:
			self.balken.itemconfig(self.fuellung_berechnet, state="hidden")
		self._zeichne_raster(breite)
		if self._marke is not None:
			# Mindestens 1 px vom Rand, sonst verschwindet die Linie im Rahmen.
			x = min(max(1.0, breite * self._marke / 100.0), max(breite - 1.0, 1.0))
			self.balken.coords(self.marke_linie, x, 0, x, self.hoehe)
			self.balken.itemconfig(self.marke_linie, state="normal")
		else:
			self.balken.itemconfig(self.marke_linie, state="hidden")


# ------------------------------------------------------------ Hauptprogramm
class UsageFenster(tk.Tk):
	def __init__(self):
		super().__init__()
		self.title(FENSTER_TITEL)
		self.configure(bg=FARBEN["bg"])

		self.ergebnisse = queue.Queue()
		self.laeuft = True
		self.letzte_aktualisierung = None
		self._naechster_abruf = None    # Kennung des geplanten after()-Aufrufs
		self._laedt = False             # verhindert mehrere Abrufe gleichzeitig
		self._drosselungen = 0          # Zähler für die Wartezeit nach HTTP 429
		self._naechster_lokal = None    # Kennung des lokalen 30-Sekunden-Takts
		self._scannt = False            # ein lokaler Scan läuft gerade
		self._server = {}               # zuletzt bestätigte Werte je Limit
		self._fenster = None            # Zeitfenster des letzten echten Abrufs
		self._basis = {}                # (Prozent, Tokens) für die Fortschreibung
		self._fehler = None             # (Textschlüssel, *Argumente) oder None
		self._wartet_bis = None         # Zeitpunkt des nächsten Versuchs nach 429
		self._letzte_limits = None      # für das Neuzeichnen beim Sprachwechsel
		self._beschriftungen = []       # (Widget, Textschlüssel, Option)
		self._balken_texte = {}         # Balkenname -> Textschlüssel

		self.einstellungen = lies_einstellungen()
		setze_sprache(self.einstellungen.get("sprache"))
		self.kompakt = bool(self.einstellungen.get("kompakt", False))
		self._groessen = {
			"voll": tuple(self.einstellungen.get("groesse_voll") or GROESSE_VOLL),
			"kompakt": tuple(self.einstellungen.get("groesse_kompakt") or GROESSE_KOMPAKT),
		}

		self._schriften()
		self._oberflaeche()
		self._setze_ansicht(self.kompakt, beim_start=True)

		self.bind("<Double-Button-1>", lambda _e: self._ansicht_umschalten())
		self.bind("<F5>", lambda _e: self.aktualisieren())
		self.protocol("WM_DELETE_WINDOW", self._beenden)
		self.after(100, self._pruefe_ergebnisse)
		self.after(1000, self._sekundentakt)
		self._naechster_lokal = self.after(LOKAL_TAKT_SEK * 1000, self._lokal_auffrischen)
		self.aktualisieren()

	# ---------------------------------------------------------- Aufbau
	def _schriften(self):
		familie = "Segoe UI" if os.name == "nt" else "DejaVu Sans"
		self.f_normal = tkfont.Font(family=familie, size=9)
		self.f_klein = tkfont.Font(family=familie, size=8)
		self.f_titel = tkfont.Font(family=familie, size=11, weight="bold")
		self.f_gross = tkfont.Font(family=familie, size=20, weight="bold")
		# Prozentwert der Kompaktansicht: hervorgehoben, aber so flach, dass
		# darunter noch der Wochenbalken Platz hat
		self.f_mittel = tkfont.Font(family=familie, size=14, weight="bold")
		self.f_mono = tkfont.Font(family="Consolas" if os.name == "nt" else "DejaVu Sans Mono",
		                          size=9)
		self.option_add("*Font", self.f_normal)

	def _beschrifte(self, widget, schluessel, option="text"):
		"""Text setzen und das Widget für den Sprachwechsel vormerken."""
		self._beschriftungen.append((widget, schluessel, option))
		widget.config(**{option: t(schluessel)})
		return widget

	def _haekchen(self, master, schluessel, variable, befehl):
		"""Kontrollkästchen im dunklen Farbschema."""
		knopf = tk.Checkbutton(master, variable=variable, command=befehl,
		                       bg=FARBEN["bg"], fg=FARBEN["text_grau"],
		                       selectcolor=FARBEN["karte"], activebackground=FARBEN["bg"],
		                       activeforeground=FARBEN["text"], font=self.f_klein,
		                       bd=0, highlightthickness=0)
		return self._beschrifte(knopf, schluessel)

	def _karte(self, master, schluessel):
		"""Ein abgesetzter Block mit Überschrift."""
		rahmen = tk.Frame(master, bg=FARBEN["karte"], padx=12, pady=10)
		rahmen.pack(fill="x", pady=(0, 10))
		if schluessel:
			kopf = tk.Label(rahmen, bg=FARBEN["karte"], fg=FARBEN["akzent"],
			                font=self.f_klein, anchor="w")
			self._beschrifte(kopf, schluessel)
			kopf.pack(fill="x", pady=(0, 8))
		return rahmen

	def _sprachwahl(self, master):
		"""Umschalter DE/EN; die aktive Sprache steht hervorgehoben."""
		rahmen = tk.Frame(master, bg=FARBEN["bg"])
		self.sprach_knoepfe = {}
		for kuerzel in SPRACHEN:
			knopf = tk.Label(rahmen, text=kuerzel.upper(), bg=FARBEN["bg"],
			                 font=self.f_klein, cursor="hand2", padx=2)
			knopf.pack(side="left")
			knopf.bind("<Button-1>", lambda _e, k=kuerzel: self._setze_sprache(k))
			self.sprach_knoepfe[kuerzel] = knopf
		self._faerbe_sprachwahl()
		return rahmen

	def _faerbe_sprachwahl(self):
		for kuerzel, knopf in getattr(self, "sprach_knoepfe", {}).items():
			knopf.config(fg=FARBEN["akzent"] if kuerzel == SPRACHE else FARBEN["text_grau"])

	def _setze_sprache(self, kuerzel):
		"""Sprache wechseln und alle Texte neu setzen."""
		if kuerzel == SPRACHE:
			return
		setze_sprache(kuerzel)
		self._faerbe_sprachwahl()
		for widget, schluessel, option in self._beschriftungen:
			widget.config(**{option: t(schluessel)})
		for name, balken in self.balken.items():
			balken.setze_titel(t(self._balken_texte.get(name, name)))
		# Alles, was aus Daten entsteht, neu aufbauen.
		self._aktualisiere_balken()
		self._zeige_verteilung()
		self._zeige_tokens()
		self._zeige_kompakt()
		self._zeige_status()
		self._hoehe_anpassen()

	def _oberflaeche(self):
		aussen = self.aussen = tk.Frame(self, bg=FARBEN["bg"], padx=12, pady=12)
		aussen.pack(fill="both", expand=True)

		# Schalterzeile: in beiden Ansichten sichtbar
		self.kopfzeile = tk.Frame(aussen, bg=FARBEN["bg"])
		self.kopfzeile.pack(fill="x", pady=(0, 8))
		self.kompakt_var = tk.BooleanVar(value=self.kompakt)
		self._haekchen(self.kopfzeile, "kompakt", self.kompakt_var,
		               lambda: self._setze_ansicht(self.kompakt_var.get())).pack(side="left")
		self.immer_oben = tk.BooleanVar(value=bool(self.einstellungen.get("immer_oben")))
		self.attributes("-topmost", self.immer_oben.get())
		self._haekchen(self.kopfzeile, "vordergrund", self.immer_oben,
		               lambda: self.attributes("-topmost",
		                                       self.immer_oben.get())).pack(side="left",
		                                                                    padx=(10, 0))
		self._sprachwahl(self.kopfzeile).pack(side="left", padx=(10, 0))
		self.status = tk.Label(self.kopfzeile, text=t("laden"), bg=FARBEN["bg"],
		                       fg=FARBEN["text_grau"], font=self.f_klein)
		self.status.pack(side="right")

		# Beide Ansichten liegen im selben Container; sichtbar ist immer genau eine.
		self.inhalt = tk.Frame(aussen, bg=FARBEN["bg"])
		self.inhalt.pack(fill="both", expand=True)
		self.bereich_voll = tk.Frame(self.inhalt, bg=FARBEN["bg"])
		self.bereich_kompakt = tk.Frame(self.inhalt, bg=FARBEN["bg"])

		self._baue_vollansicht(self.bereich_voll)
		self._baue_kompaktansicht(self.bereich_kompakt)
		self._baue_fusszeile(aussen)

	def _baue_vollansicht(self, aussen):
		# --- Limits
		karte = self._karte(aussen, "karte_limits")
		self.balken = {}
		for schluessel, textschluessel in (("session", "limit_session"),
		                                   ("weekly", "limit_weekly")):
			balken = Limitbalken(karte, t(textschluessel))
			balken.pack(fill="x", pady=(0, 10))
			self.balken[schluessel] = balken
			self._balken_texte[schluessel] = textschluessel
		# Weitere Limits (z. B. Opus-Woche) werden bei Bedarf ergaenzt.
		self.limit_karte = karte

		# --- Aufteilung der Woche
		self.verteilung_karte = self._karte(aussen, "karte_verteilung")
		self.verteilung = tk.Label(self.verteilung_karte, text="–", bg=FARBEN["karte"],
		                           fg=FARBEN["text"], justify="left", anchor="w",
		                           font=self.f_klein)
		self.verteilung.pack(fill="x")

		# --- Tokens
		token_karte = self._karte(aussen, "karte_tokens")
		self.token_auswahl = tk.StringVar(value="session")
		schalter = tk.Frame(token_karte, bg=FARBEN["karte"])
		schalter.pack(fill="x", pady=(0, 8))
		for wert, textschluessel in (("session", "wahl_session"), ("weekly", "wahl_weekly")):
			knopf = tk.Radiobutton(schalter, value=wert,
			                       variable=self.token_auswahl, command=self._zeige_tokens,
			                       bg=FARBEN["karte"], fg=FARBEN["text"],
			                       selectcolor=FARBEN["bg"], activebackground=FARBEN["karte"],
			                       activeforeground=FARBEN["text"], font=self.f_klein,
			                       bd=0, highlightthickness=0)
			self._beschrifte(knopf, textschluessel).pack(side="left", padx=(0, 10))
		self.token_text = tk.Label(token_karte, text="–", bg=FARBEN["karte"],
		                           fg=FARBEN["text"], justify="left", anchor="w",
		                           font=self.f_mono)
		self.token_text.pack(fill="x")

	def _baue_kompaktansicht(self, aussen):
		"""
		Das 5-Stunden-Limit groß, darunter der Wochenbalken mit Tagesraster
		und Soll-Marke – knapp beschriftet, damit die Fensterhöhe reicht.
		Countdown und Soll-Ist-Vergleich der Woche stehen in der Vollansicht.
		"""
		karte = tk.Frame(aussen, bg=FARBEN["karte"], padx=10, pady=3)
		karte.pack(fill="both", expand=True)

		balken = Limitbalken(karte, t("limit_session"),
		                     titel_font=self.f_klein, wert_font=self.f_mittel,
		                     fuss_font=self.f_klein, hoehe=12, knapp=True)
		balken.pack(fill="x")
		self.balken["session_kompakt"] = balken
		self._balken_texte["session_kompakt"] = "limit_session"

		woche = Limitbalken(karte, t("wahl_weekly"),
		                    titel_font=self.f_klein, wert_font=self.f_klein,
		                    fuss_font=self.f_klein, hoehe=10,
		                    knapp=True, nur_balken=True)
		woche.pack(fill="x", pady=(4, 0))
		self.balken["weekly_kompakt"] = woche
		self._balken_texte["weekly_kompakt"] = "wahl_weekly"

		self.kompakt_rest = tk.Label(karte, text="", bg=FARBEN["karte"],
		                             fg=FARBEN["text_grau"], anchor="w",
		                             font=self.f_klein, pady=0, bd=0)
		self.kompakt_rest.pack(fill="x", pady=(3, 0))

	def _baue_fusszeile(self, aussen):
		"""Nur in der Vollansicht; kompakt genügt der 5-Minuten-Takt bzw. F5."""
		self.fusszeile = tk.Frame(aussen, bg=FARBEN["bg"])
		self._beschrifte(ttk.Button(self.fusszeile, command=self.aktualisieren),
		                 "aktualisieren").pack(side="left")

	# ---------------------------------------------------------- Ansichten
	def _ansicht_umschalten(self):
		self._setze_ansicht(not self.kompakt)

	def _merke_groesse(self):
		"""Aktuelle Fenstergröße der gerade sichtbaren Ansicht sichern."""
		if not self.winfo_ismapped():
			return                      # Fenster noch nicht gezeichnet
		# Erst nach einem Layout-Durchlauf liefert winfo_* die neuen Maße.
		self.update_idletasks()
		breite, hoehe = self.winfo_width(), self.winfo_height()
		if breite > 50 and hoehe > 50:
			self._groessen["kompakt" if self.kompakt else "voll"] = (breite, hoehe)

	def _setze_ansicht(self, kompakt, beim_start=False):
		"""Zwischen Vollansicht und reiner 5-Stunden-Anzeige wechseln."""
		if not beim_start:
			self._merke_groesse()
		self.kompakt = kompakt

		self.kompakt_var.set(kompakt)   # Häkchen nachziehen (Doppelklick, Start)
		self.bereich_voll.pack_forget()
		self.bereich_kompakt.pack_forget()
		if kompakt:
			# Knapper Rahmen, damit über der Beschriftung und unter der
			# Tokenzeile möglichst wenig Luft bleibt.
			self.aussen.config(padx=8, pady=4)
			self.kopfzeile.pack_configure(pady=(0, 2))
			self.fusszeile.pack_forget()
			self.bereich_kompakt.pack(fill="both", expand=True)
			self.minsize(330, 110)
		else:
			self.aussen.config(padx=12, pady=12)
			self.kopfzeile.pack_configure(pady=(0, 8))
			self.fusszeile.pack(fill="x", side="bottom", pady=(10, 0))
			self.bereich_voll.pack(fill="both", expand=True)
			self.minsize(400, 450)

		breite, hoehe = self._groessen["kompakt" if kompakt else "voll"]
		if kompakt:
			# Höhe genau am Inhalt ausrichten, sonst sammelt sich der
			# überschüssige Platz als Leerraum unter der letzten Zeile.
			self.update_idletasks()
			hoehe = max(self.winfo_reqheight(), 110)
		self.geometry("%dx%d" % (breite, hoehe))
		self._zeige_kompakt()

	# -------------------------------------------------------- Datenabruf
	def aktualisieren(self):
		if self._laedt:
			return                      # ein Abruf ist bereits unterwegs
		# Eine bereits eingeplante Aktualisierung verwerfen, damit ein Klick auf
		# "Aktualisieren" den Takt verschiebt statt eine zweite Kette zu starten.
		if self._naechster_abruf is not None:
			self.after_cancel(self._naechster_abruf)
			self._naechster_abruf = None
		self._laedt = True
		self._zeige_status()
		threading.Thread(target=self._lade_im_hintergrund, daemon=True).start()

	def _lade_im_hintergrund(self):
		daten = {}
		try:
			token = lies_zugriffstoken()
			if not token:
				raise KeineAnmeldung
			daten["limits"] = hole_limits(token)
		# Fehler werden als (Textschlüssel, *Argumente) gemeldet, damit sie sich
		# beim Sprachwechsel neu übersetzen lassen.
		except KeineAnmeldung:
			daten["fehler"] = ("fehler_anmeldung",)
		except urllib.error.HTTPError as fehler:
			if fehler.code in (401, 403):
				daten["fehler"] = ("fehler_token",)
			elif fehler.code == 429:
				# Drosselung: der Server nennt teils selbst eine Wartezeit.
				daten["gedrosselt"] = True
				daten["fehler"] = ("fehler_drosselung",)
				try:
					daten["retry_after"] = int(fehler.headers.get("retry-after", ""))
				except (TypeError, ValueError):
					daten["retry_after"] = None
			else:
				daten["fehler"] = ("fehler_http", fehler.code)
		except Exception as fehler:                       # noqa: BLE001
			# Unerwartetes bleibt im Wortlaut der Ausnahme stehen.
			daten["fehler"] = (None, str(fehler))

		# Zeitfenster aus den Limits ableiten, sonst mit Standardlängen rechnen.
		jetzt = datetime.now(timezone.utc)
		limits = daten.get("limits") or {}
		fuenf = (limits.get("five_hour") or {})
		sieben = (limits.get("seven_day") or {})
		session_reset = _zeitstempel(fuenf.get("resets_at"))
		session_start = (session_reset - timedelta(hours=5)) if session_reset else jetzt - timedelta(hours=5)
		wochen_start = _zeitstempel((limits.get("seven_day_breakdown") or {}).get("window_started_at"))
		if wochen_start is None:
			wochen_reset = _zeitstempel(sieben.get("resets_at"))
			wochen_start = (wochen_reset - timedelta(days=7)) if wochen_reset else jetzt - timedelta(days=7)

		daten["fenster"] = {"session": session_start, "weekly": wochen_start}
		try:
			daten["tokens"] = zaehle_tokens(daten["fenster"])
		except Exception:                                 # noqa: BLE001
			daten["tokens"] = {}

		self.ergebnisse.put(daten)

	# ------------------------------------------------- lokale Fortschreibung
	def _lokal_auffrischen(self):
		"""
		30-Sekunden-Takt ohne Netzzugriff: zählt nur die Transcripts neu und
		schreibt daraus den Prozentwert fort, bis der nächste echte Abruf kommt.
		"""
		self._naechster_lokal = None
		reset = (self._server.get("session") or (None, None))[1]
		if reset is not None and datetime.now(timezone.utc) >= reset:
			# Das 5-Stunden-Fenster ist umgesprungen; fortschreiben wäre falsch.
			self.aktualisieren()
		elif self._fenster and not self._laedt and not self._scannt:
			self._scannt = True
			threading.Thread(target=self._scanne_lokal, daemon=True).start()
		if self.laeuft:
			self._naechster_lokal = self.after(LOKAL_TAKT_SEK * 1000,
			                                   self._lokal_auffrischen)

	def _scanne_lokal(self):
		try:
			tokens = zaehle_tokens(self._fenster)
		except Exception:                                 # noqa: BLE001
			tokens = None
		self.ergebnisse.put({"nur_lokal": True, "tokens": tokens})

	def _berechne_prozent(self, name):
		"""
		Prozentwert aus dem lokalen Tokenzuwachs hochrechnen: Wie viele Tokens
		einem Prozentpunkt entsprechen, verrät der letzte echte Abruf.
		"""
		basis = self._basis.get(name)
		if not basis:
			return None
		prozent_basis, tokens_basis = basis
		if not prozent_basis or tokens_basis <= 0:
			return None
		jetzt = summe_tokens(getattr(self, "tokens", {}).get(name, {}))
		if jetzt <= tokens_basis:
			return None
		return prozent_basis * jetzt / tokens_basis

	def _pruefe_ergebnisse(self):
		try:
			while True:
				self._uebernehmen(self.ergebnisse.get_nowait())
		except queue.Empty:
			pass
		if self.laeuft:
			self.after(200, self._pruefe_ergebnisse)

	def _hoehe_anpassen(self):
		"""
		Das Fenster nie kleiner zeigen, als der Inhalt braucht: Tokenliste
		und Planzeile wachsen erst mit den Daten, die untere Zeile wäre sonst
		abgeschnitten. Nur vergrößern – eine größere Wunschhöhe bleibt erhalten.
		"""
		if not self.winfo_ismapped():
			return
		self.update_idletasks()
		noetig = self.winfo_reqheight()
		if noetig > self.winfo_height():
			self.geometry("%dx%d" % (self.winfo_width(), noetig))

	# -------------------------------------------------------- Darstellung
	def _uebernehmen(self, daten):
		if daten.get("nur_lokal"):
			# Ergebnis des 30-Sekunden-Scans: nur die Tokenzahlen sind neu,
			# die Limits bleiben auf dem zuletzt bestätigten Stand.
			self._scannt = False
			if daten.get("tokens"):
				self.tokens = daten["tokens"]
				self._aktualisiere_balken()
				self._zeige_tokens()
				self._zeige_kompakt()
				self._hoehe_anpassen()
			return

		self._laedt = False
		self.tokens = daten.get("tokens", {})
		self._fenster = daten.get("fenster") or self._fenster
		self._fehler = daten.get("fehler")
		self._wartet_bis = None

		if daten.get("gedrosselt"):
			# Nach jeder weiteren Drosselung länger warten, aber eine vom Server
			# genannte Wartezeit hat Vorrang.
			stufe = min(self._drosselungen, len(WARTE_NACH_DROSSELUNG) - 1)
			self._drosselungen += 1
			wartezeit = daten.get("retry_after") or WARTE_NACH_DROSSELUNG[stufe]
			self._wartet_bis = datetime.now() + timedelta(seconds=wartezeit)
		else:
			wartezeit = AKTUALISIERUNG_SEK
			if not self._fehler:
				self._drosselungen = 0

		if not self._fehler:
			self.letzte_aktualisierung = datetime.now()
		self._zeige_status()

		# Bei einem Fehler bleiben die zuletzt geholten Balken stehen; die lokal
		# gezählten Tokens sind davon unabhängig und werden trotzdem aufgefrischt.
		limits = daten.get("limits")
		if limits:
			self._zeige_limits(limits)
		self._zeige_tokens()
		self._zeige_kompakt()
		self._hoehe_anpassen()

		if self.laeuft:
			self._naechster_abruf = self.after(int(wartezeit * 1000), self.aktualisieren)

	def _fehlertext(self):
		"""Letzte Fehlermeldung in der aktuellen Sprache, sonst None."""
		if not self._fehler:
			return None
		schluessel, argumente = self._fehler[0], self._fehler[1:]
		text = t(schluessel, *argumente) if schluessel else str(argumente[0])
		if self._wartet_bis is not None:
			text = t("fehler_erneut", text, self._wartet_bis.strftime("%H:%M"))
		return text

	def _zeige_status(self):
		"""Kopfzeile rechts: Ladehinweis, Fehler oder Zeit des letzten Abrufs."""
		fehler = self._fehlertext()
		if self._laedt:
			self.status.config(text=t("laden"), fg=FARBEN["text_grau"])
		elif fehler:
			self.status.config(text=fehler, fg=FARBEN["rot"])
		elif self.letzte_aktualisierung is not None:
			self.status.config(text=t("stand",
			                          self.letzte_aktualisierung.strftime("%H:%M:%S")),
			                   fg=FARBEN["text_grau"])

	def _zeige_limits(self, limits):
		self._letzte_limits = limits
		fuenf = limits.get("five_hour") or {}
		sieben = limits.get("seven_day") or {}
		self._server = {
			"session": (fuenf.get("utilization") or 0, _zeitstempel(fuenf.get("resets_at"))),
			"weekly": (sieben.get("utilization") or 0, _zeitstempel(sieben.get("resets_at"))),
		}
		# Bezugspunkt der Fortschreibung: So viele Tokens entsprachen diesem
		# Prozentwert im Moment des Abrufs.
		self._basis = {name: (prozent, summe_tokens(self.tokens.get(name, {})))
		               for name, (prozent, _reset) in self._server.items()}
		# für die Hochrechnung des verbleibenden Kontingents
		self.prozente = {name: prozent for name, (prozent, _r) in self._server.items()}
		self._aktualisiere_balken()

		# Zusätzliche Limits, sofern der Tarif welche liefert (z. B. Opus-Woche).
		for schluessel, textschluessel in (("seven_day_opus", "limit_opus"),
		                                   ("seven_day_sonnet", "limit_sonnet")):
			eintrag = limits.get(schluessel)
			if not eintrag:
				continue
			if schluessel not in self.balken:
				balken = Limitbalken(self.limit_karte, t(textschluessel))
				balken.pack(fill="x", pady=(0, 10))
				self.balken[schluessel] = balken
				self._balken_texte[schluessel] = textschluessel
			self.balken[schluessel].setze(eintrag.get("utilization", 0),
			                              _zeitstempel(eintrag.get("resets_at")),
			                              marke=self._wochen_soll(),
			                              raster=self._wochen_tage())

		self._zeige_verteilung()

	def _zeige_verteilung(self):
		"""Aufteilung der Woche nach Anwendung; die Namen liefert die API."""
		limits = self._letzte_limits or {}
		zeilen = []
		for zeile in (limits.get("seven_day_breakdown") or {}).get("rows", []):
			if zeile.get("percent"):
				zeilen.append("%s %d %%" % (zeile.get("display_name", zeile.get("key")),
				                            zeile["percent"]))
		text = "   ·   ".join(zeilen) if zeilen else t("keine_daten")

		zusatz = limits.get("extra_usage") or {}
		if zusatz.get("is_enabled") and zusatz.get("utilization") is not None:
			text += "\n" + t("zusatzguthaben", zusatz["utilization"])
		self.verteilung.config(text=text)

	def _wochen_soll(self):
		"""
		Wie viel vom Wochenlimit nach verstrichener Zeit verbraucht sein darf,
		damit es gleichmäßig bis zum Reset reicht (rund 14 % je Tag).
		"""
		reset = (self._server.get("weekly") or (None, None))[1]
		start = (self._fenster or {}).get("weekly")
		if reset is None or start is None:
			return None
		gesamt = (reset - start).total_seconds()
		if gesamt <= 0:
			return None
		verstrichen = (datetime.now(timezone.utc) - start).total_seconds()
		return max(0.0, min(100.0, verstrichen / gesamt * 100.0))

	def _wochen_tage(self):
		"""Länge des Wochenfensters in ganzen Tagen – Raster für die Balken."""
		reset = (self._server.get("weekly") or (None, None))[1]
		start = (self._fenster or {}).get("weekly")
		if reset is None or start is None:
			return 7
		tage = int(round((reset - start).total_seconds() / 86400.0))
		# Nur plausible Längen übernehmen, sonst beim bekannten Wochenfenster bleiben.
		return tage if 2 <= tage <= 31 else 7

	def _ist_wochenbalken(self, name):
		name = name.removesuffix("_kompakt")
		return name == "weekly" or name.startswith("seven_day")

	def _aktualisiere_balken(self):
		"""Balken neu setzen: bestätigter Serverwert plus lokale Fortschreibung."""
		soll = self._wochen_soll()
		tage = self._wochen_tage()
		for name, (prozent, reset) in self._server.items():
			berechnet = self._berechne_prozent(name)
			woche = self._ist_wochenbalken(name)
			for schluessel in (name, name + "_kompakt"):
				if schluessel in self.balken:
					self.balken[schluessel].setze(prozent, reset, berechnet,
					                              marke=soll if woche else None,
					                              raster=tage if woche else 0)

	def _zeige_tokens(self):
		fenster = self.token_auswahl.get()
		je_modell = getattr(self, "tokens", {}).get(fenster, {})
		if not je_modell:
			self.token_text.config(text=t("keine_tokens"))
			return

		zeilen = []
		summe = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
		for modell, werte in sorted(je_modell.items(),
		                            key=lambda p: -(p[1]["output"] + p[1]["input"])):
			for schluessel in summe:
				summe[schluessel] += werte[schluessel]
			zeilen.append("%-11s %9s %-3s %9s %s" % (modellname(modell)[:11],
			                                         formatiere_anzahl(werte["input"] + werte["cache_write"]),
			                                         t("spalte_ein"),
			                                         formatiere_anzahl(werte["output"]),
			                                         t("spalte_aus")))
		gesamt = summe["input"] + summe["output"] + summe["cache_write"]
		zeilen.append("")
		zeilen.append("%-11s %9s" % (t("cache_lesen"), formatiere_anzahl(summe["cache_read"])))
		zeilen.append("%-11s %9s" % (t("verarbeitet"), formatiere_anzahl(gesamt)))

		# Das Limit selbst wird nur in Prozent veröffentlicht. Aus dem gezählten
		# Verbrauch lässt sich der Rest hochrechnen – eine Schätzung, weil in die
		# Limits auch Nutzung außerhalb von Claude Code einfließt.
		prozent = self._berechne_prozent(fenster) or getattr(self, "prozente", {}).get(fenster)
		if prozent:
			rest = gesamt * (100.0 - prozent) / prozent
			zeilen.append("%-11s %9s" % (t("rest_ca"), formatiere_anzahl(rest)))
		self.token_text.config(text="\n".join(zeilen))

	def _zeige_kompakt(self):
		"""Zusatzzeile der Kompaktansicht: Verbrauch, Rest und etwaige Fehler."""
		if not hasattr(self, "kompakt_rest"):
			return
		fehler = self._fehlertext()
		if fehler:
			self.kompakt_rest.config(text=fehler, fg=FARBEN["rot"])
			return

		je_modell = getattr(self, "tokens", {}).get("session", {})
		gesamt = summe_tokens(je_modell)
		prozent = self._berechne_prozent("session") or getattr(self, "prozente", {}).get("session")
		zeilen = []
		if gesamt:
			zeile = t("kompakt_verbrauch", formatiere_anzahl(gesamt))
			if prozent:
				zeile += "  ·  " + t("kompakt_rest",
				                     formatiere_anzahl(gesamt * (100.0 - prozent) / prozent))
			zeilen.append(zeile)
		# Der Zeitstempel steht bereits oben in der Statusanzeige.
		self.kompakt_rest.config(text="\n".join(zeilen) or "–", fg=FARBEN["text_grau"],
		                         justify="left")

	def _sekundentakt(self):
		soll = self._wochen_soll()
		for name, balken in getattr(self, "balken", {}).items():
			if soll is not None and self._ist_wochenbalken(name):
				balken.setze_marke(soll)
			balken.aktualisiere_countdown()
		if self.laeuft:
			self.after(1000, self._sekundentakt)

	def _beenden(self):
		self.laeuft = False
		for kennung in ("_naechster_abruf", "_naechster_lokal"):
			if getattr(self, kennung) is not None:
				self.after_cancel(getattr(self, kennung))
				setattr(self, kennung, None)
		self._merke_groesse()
		schreibe_einstellungen({
			"sprache": SPRACHE,
			"kompakt": self.kompakt,
			"immer_oben": bool(self.immer_oben.get()),
			"groesse_voll": list(self._groessen["voll"]),
			"groesse_kompakt": list(self._groessen["kompakt"]),
		})
		self.destroy()


if __name__ == "__main__":
	UsageFenster().mainloop()
