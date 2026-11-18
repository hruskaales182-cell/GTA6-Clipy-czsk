# Klipárna – nasazení (jednou, ~20 minut)

Co to je:
- **appka v mobilu**: Cloudflare Worker + úložiště R2, zdarma
- **denní stříhání**: GitHub Actions, každý den 19:00, zdarma
- volitelně **Claude** na výběr momentů, háčky a hledání kamery (odhadem pár set Kč měsíčně)

Počítač nemusí být zapnutý.

## 1. Cloudflare (appka + videa)
1. Založ si účet na https://dash.cloudflare.com (zdarma). V levém menu otevři **R2** a zapni ho. Cloudflare u R2 může chtít platební kartu, i když 10 GB je zdarma. Zadáváš ji ty, ne já.
2. Pak napiš mně do chatu „Cloudflare hotovo“. Já spustím `npx wrangler login` a ty se jen přihlásíš v okně prohlížeče, které se otevře.
3. Pak udělám sám:
   - založím úložiště (`clipapp-media`) a databázi (KV),
   - nahraju appku.
4. Nastavíš **heslo do appky**: napíšeš ho do terminálu, já ho nevidím. Vygeneruju i tajný token pro denní běh.
5. Dostaneš adresu typu `https://clipapp.<tvuj-ucet>.workers.dev`.

## 2. GitHub (denní běh)
1. Založ si účet na https://github.com, pokud ho nemáš.
2. Vytvoř soukromý repozitář `klipana`. Já do něj nahraju obsah složky `clipapp`.
3. V repozitáři otevři **Settings → Secrets and variables → Actions** a přidej:
   - `KLIPARNA_URL` = adresa appky z kroku 1
   - `PIPELINE_TOKEN` = token z kroku 1
   - `ANTHROPIC_API_KEY` = tvůj klíč z https://console.anthropic.com. Je volitelný, ale doporučuju ho, bez něj jsou klipy hloupější.
4. V záložce **Actions → Denní klipy → Run workflow** můžeš běh spustit ručně kdykoliv.

## 3. iPhone
1. Otevři adresu appky v **Safari**.
2. Dej **Sdílet → Přidat na plochu**. Appka bude mít ikonu a otevírá se přes celou obrazovku jako normální aplikace.
3. Přihlas se heslem. Přihlášení vydrží 60 dní.

## Jak to pak funguje
- Každý den v 19:00 se stáhnou nejlepší klipy za 24 h od streamerů v `pipeline/sources.json`, sestříhají se a objeví se v záložce **Nové**.
- ✓ Schválit / ✕ Zahodit → ve **Schválených** je tlačítko **Stáhnout video**. Na iPhonu pak dáš „Uložit video“ a nahraješ ho přes aplikaci YouTube. Název a popis zkopíruješ tlačítkem.
- Po nahrání dáš **🚀 Nahráno**.
- V záložce **Napiš mi** můžeš psát přání, třeba „víc Herdyna“. Claude je při dalším běhu bere v úvahu.

## Limity a rizika
- **Kick** (Haiset, Fattypillow) často blokuje servery GitHubu. Twitch streameři (Herdyn, Kai, Speed, ohnePixel) fungují spolehlivě.
- Přepis řeči běží na slabším serveru, takže **české titulky před nahráním zkontroluj**.
- Automatické nahrávání na YouTube zatím není. Přes API by videa byla soukromá, dokud aplikace neprojde auditem u Googlu.
