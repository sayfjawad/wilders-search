# Van idee naar live AI-archief in één dag — waarom jaren aan opgebouwde infrastructuur het verschil maken

Gisteren was het een idee. Vandaag staat het live: een slim doorzoekbaar archief van álle openbare uitspraken van Geert Wilders — van zijn eerste Kamerbijdragen in 1995 tot de debatten van vorige maand. Ruim **371.000 tekstfragmenten uit meer dan 1.200 bronnen**, semantisch doorzoekbaar, elk fragment met datum, tijdstip, spreker en een afspeellink naar de originele bron.

Je stelt een vraag in gewone taal — "wat zei hij over de hypotheekrenteaftrek?" — en je krijgt niet alleen de relevante fragmenten door de jaren heen, maar ook een AI-samenvatting met bronverwijzingen die je stuk voor stuk kunt controleren. Geen quotes uit de losse pols, maar verifieerbare context. Dat is het hele punt: transparantie waar je op kunt klikken.

## De techniek

Onder de motorkap komen een paar werelden samen:

**Open data als fundament.** De officiële Kamerverslagen komen uit het open datamagazijn van de Tweede Kamer (OData API + vlos-XML), aangevuld met de gedigitaliseerde Handelingen van Officiële Bekendmakingen voor de periode 1995–2013. Parsen van die XML is een vak apart: sprekerslabels, interrupties, gecorrigeerde versus ongecorrigeerde verslagen — het zit vol randgevallen.

**Spraakherkenning op eigen GPU's.** Honderden openbare video's zijn getranscribeerd met WhisperX, verdeeld in shards over drie GPU-machines die parallel draaiden. Geen cloud-API's: alles on-premise, ook het taalmodel dat de samenvattingen schrijft (llama.cpp met een 14B-model, lokaal).

**Semantisch zoeken.** Alle fragmenten zijn geëmbed met BGE-M3, een meertalig embeddingmodel. Daardoor vind je op betékenis, niet alleen op letterlijke woorden. De zoeklaag draait GPU-versneld achter een FastAPI-app.

**Video op de seconde nauwkeurig.** Elk verslag-fragment draagt een wallclock-tijdstip mee. Daarmee koppelen we Kamerverslagen aan de videostreams van Debat Direct: klik op een citaat en de video springt naar het exacte moment in het debat. Het video-archief (terug tot ~2010) wordt op dit moment nog binnengehaald.

**Zelfherstellend.** Het hele systeem draait onbeheerd: systemd-services, cron-jobs die na een reboot alles hervatten, watchers die milestones per mail melden en automatisch de index herbouwen. Stroomuitval? Het herstelt zichzelf.

## De hardware

Dit draait niet in de cloud, maar op eigen ijzer: een HP Z8-workstation met twee Tesla V100's als bouw- en servermachine, een Dell C4130 met vier V100's in het datacenter, en een ARM-node met NVIDIA GB10 die meehielp met transcriberen — alles verbonden via een Tailscale-mesh, met een kleine edge-VPS die nginx en TLS verzorgt. Eigen hardware betekent: geen API-kosten per token, geen datalimieten, en volledige controle over de data.

## De echte les: accumulatie

En nu het eerlijke verhaal. Dat dit in één dag kon, is géén kwestie van snel typen.

Dit archief staat op de schouders van **scrib-r**, een transcriptieplatform dat ik al jaren ontwikkel. Al die jaren aan opgebouwde kennis zaten er al: hoe je ASR-pipelines betrouwbaar maakt, hoe je GPU-workers orkestreert, hoe je modelcaches offline en reproduceerbaar houdt, hoe je een mediastreaming-proxy configureert, welke valkuilen er in OData-paginering en HLS-streams zitten. Een eerder archiefproject op hetzelfde platform leverde de beproefde architectuur die ik hier kon hergebruiken.

De eerste keer kostte dit soort werk maanden. Nu één dag — plus een paar dagen achtergrondwerk voor de pipelines die zichzelf beheren.

Dat is de onderschatte kant van software-engineering: de waarde zit niet alleen in het product van vandaag, maar in de gecumuleerde infrastructuur, kennis en littekens van alle projecten ervoor. Elke opgeloste bug, elk gedocumenteerd randgeval, elke herbruikbare module verlaagt de kosten van het vólgende idee. Op een gegeven moment kantelt het: dan is een compleet nieuw platform ineens een dagproject.

## Herbruikbaar voor iedereen

Het systeem is bewust persoons-onafhankelijk gebouwd: één configuratiebestand bepaalt wiens openbare uitspraken worden gearchiveerd. Dezelfde pipeline werkt voor elke publieke figuur — omdat publieke verantwoording gebaat is bij vindbare, verifieerbare bronnen.

Kanttekening die erbij hoort: de Kamerverslagen zijn de officiële, gecorrigeerde transcripten; de videotranscripties zijn machinaal gegenereerd en kunnen fouten bevatten. Daarom linkt elk fragment naar de bron — controleer belangrijke citaten altijd zelf.

Benieuwd? Het archief is te vinden op **wilders.scrib-r.com**.

*Vragen over de aanpak, de open-datapipelines of de hardware-setup? Stel ze gerust in de comments.*
