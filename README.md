# Selettore foto

Web app per esaminare una raccolta di fotografie, individuare immagini potenzialmente sfocate e riconoscere foto simili, direttamente dal browser.

**Apri l'app:** https://pepcoiro.github.io/selettore-foto/

## Funzionalità

- Carica immagini dal computer selezionando file o trascinando le foto nella pagina.
- Analizza la nitidezza e segnala le immagini che potrebbero essere sfocate.
- Evidenzia immagini molto scure o con possibili alte luci eccessive.
- Confronta le immagini tramite hash percettivi per individuare foto simili.
- Filtra e ordina i risultati e seleziona manualmente le foto da conservare.
- Propone una selezione iniziale basata sulla nitidezza: controlla sempre il risultato prima di esportare.
- Esporta le foto selezionate in una cartella, quando il browser supporta questa funzione, oppure scaricale in un archivio ZIP.

## Come si usa

1. Apri [Selettore foto](https://pepcoiro.github.io/selettore-foto/) nel browser.
2. Seleziona una cartella o scegli le immagini da analizzare.
3. Avvia l'analisi e attendi che sia completata.
4. Controlla nitidezza, avvisi e gruppi di immagini simili.
5. Seleziona le foto desiderate ed esportale.

## Privacy e compatibilità

Le fotografie vengono lette ed elaborate nel browser: l'app non prevede il caricamento delle immagini su un servizio remoto. Per il confronto tra immagini e la creazione degli archivi ZIP vengono caricate librerie JavaScript esterne; è quindi necessaria una connessione Internet per caricare tali librerie.

È consigliato un browser aggiornato basato su Chromium, come Chrome o Edge. La scelta diretta di una cartella di destinazione dipende dal supporto del browser; se non è disponibile, usa l'esportazione ZIP.

## Note

- Il punteggio di nitidezza è un indicatore automatico, non una valutazione infallibile della qualità fotografica.
- Il rilevamento delle foto simili può non riconoscere immagini con inquadrature o modifiche molto diverse.
- Controlla sempre i risultati e la selezione prima di esportare.
- Il supporto ai formati dipende dal browser: alcuni formati, in particolare certi RAW, potrebbero non essere visualizzabili o analizzabili.
