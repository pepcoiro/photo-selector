# Photo Selector

Applicazione desktop locale per selezionare fotografie JPEG, PNG, TIFF e, opzionalmente, RAW. È pensata per uso generico e non è legata a un evento o a un'azienda.

## Funzioni
- Apre una cartella di immagini e mostra una griglia di miniature.
- Analizza la nitidezza con la varianza del Laplaciano su un’anteprima con dimensione massima fissa di 800×600, per rendere i punteggi più confrontabili. È comunque un indicatore orientativo.
- Segnala in modo indicativo foto potenzialmente sfocate e immagini molto scure o con alte luci estreme.
- Permette di selezionare e deselezionare le immagini manualmente.
- Offre una selezione iniziale delle foto senza segnalazioni e, per ogni gruppo di quasi-duplicati, mantiene come candidata la foto con l’indice di nitidezza più alto. Il risultato va sempre controllato.
- Copia o sposta le immagini selezionate in una cartella scelta.
- Evita di sovrascrivere file omonimi nella destinazione, generando un nome alternativo.
- Elabora tutto localmente: non carica le fotografie online e non le elimina automaticamente.

## Interfaccia
Tema scuro basato sulla palette: nero `#000000`, blu di Prussia `#14213D`, arancione `#FCA311`, grigio alabastro `#E5E5E5` e bianco `#FFFFFF`.

## Requisiti
- Windows 10/11.
- Python 3.10 o superiore (consigliato Python 3.12).
- Per i formati JPEG/PNG/TIFF: dipendenze elencate in `requirements.txt`.
- Per i RAW: installare anche `rawpy`; il supporto dipende dal formato e dalla fotocamera.

## Installazione
Apri il Prompt dei comandi nella cartella del progetto ed esegui:

```bat
py -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Per aggiungere il supporto RAW:

```bat
pip install rawpy
```

## Avvio

```bat
python main.py
```

## Uso
1. Premi **Apri cartella foto…** e scegli la cartella contenente le immagini.
2. Se vuoi, premi **Analizza foto** per ottenere indicazioni su nitidezza ed esposizione.
3. Seleziona le immagini cliccando sulla miniatura, sul nome o sulla casella **Seleziona**.
4. Facoltativamente, usa **Seleziona tutte le foto buone** come punto di partenza e controlla la selezione.
5. Premi **COPIA SELEZIONATE NELLA CARTELLA…** e scegli la destinazione. La copia è l'opzione più sicura perché conserva gli originali.

## Limiti
- L'indice di sfocatura è indicativo, non un giudizio certo: texture, luce, rumore e movimento intenzionale possono influenzarlo.
- Le segnalazioni di esposizione sono euristiche.
- Rileva immagini quasi identiche con hash percettivo; immagini con inquadratura molto diversa possono non essere riconosciute. Controllare i risultati.
- Il supporto RAW richiede `rawpy`.
- Questa versione non è ancora impacchettata in un file `.exe`: richiede Python e le dipendenze.


### v1.8
- Controllo della nitidezza sul volto quando rilevato; senza volto usa il 25° percentile della nitidezza di quattro zone. Questo riduce il rischio che uno sfondo dettagliato mascheri un soggetto sfocato, ma non è infallibile.
- Per ogni gruppo di quasi-duplicati viene marcata una candidata preferita in base all’indice di nitidezza; la selezione automatica non esclude più tutte le foto del gruppo.
- Analisi su anteprima massima 800×600; soglia iniziale 60 da calibrare con foto reali.


### v1.9 - correzione effettiva
- Analisi su anteprima massima 800×600; misura prima la nitidezza del volto se viene rilevato, altrimenti usa il 25° percentile della nitidezza di quattro aree.
- Per ogni gruppo di quasi-duplicati viene marcata la foto con punteggio più alto come candidata migliore; la selezione automatica non esclude più tutte le foto simili.
- Soglia iniziale 60, da calibrare su foto reali. Il punteggio è un indicatore e non una certezza sulla qualità fotografica.


### v2.0
- Soglia predefinita «Segnala sotto» impostata a 180.
- Pulsante «APRI CARTELLA FOTO» evidenziato in arancione e pannello iniziale che spiega come cominciare; la griglia appare dopo aver scelto una cartella.

### v2.1
- Analisi parallela multi-core (fino a 8x più veloce su CPU moderne) con pulsante «Ferma analisi».
- Caricamento asincrono delle miniature in background: interfaccia sempre fluida anche con centinaia di foto pesanti o RAW.
- Calcolo della nitidezza più intelligente: analisi combinata di centro e aree salienti, eliminando i falsi allarmi causati dal bokeh o sfondi sfocati.
- Cache in memoria per il rilevamento volti: rimosso l'overhead di rilettura da disco del modello Haar Cascade su ogni immagine.
- Griglia ottimizzata: il ridimensionamento della finestra non distrugge più i componenti grafici, azzerando sfarfallio e consumo di memoria.
- Risolti conflitti di stile CSS e ripristinati gli stati hover/pressed per tutti i pulsanti.
- Aggiunta funzione «Inverti selezione» (Ctrl+I).
- Finestra di avanzamento per la copia/spostamento dei file per prevenire blocchi di sistema su trasferimenti pesanti.
- Pulizia del codice: eliminati moduli inutilizzati (`hashlib`, widget non utilizzati).
- Aggiunto `.gitignore` per escludere cartelle di build e file temporanei.
