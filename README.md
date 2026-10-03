# WeddingCulling

Aplicativo Windows para seleção técnica conservadora de fotografias RAW e JPEG. O
WeddingCulling detecta rajadas, compara imagens semelhantes e gera um relatório
local. Ele não edita, move, renomeia ou exclui os originais.

## Como usar

Instale pelo arquivo `dist\installer\WeddingCulling-Setup-1.1.0-x64.exe`.
Compatível com Windows 10/11 de 64 bits; não é necessário instalar Python.

1. Abra `WeddingCulling.exe`.
2. Pressione **PROCURAR** e escolha a pasta das fotografias RAW e/ou JPEG.
3. Confira a contagem e pressione **ANALISAR FOTOGRAFIAS**.
4. Ao concluir, use **ABRIR RELATÓRIO** para consultar os resultados.
5. Opcionalmente, use **EXTRAIR FOTOGRAFIAS** para copiar categorias de arquivos
   originais completos para outra pasta.

Formatos: ARW, CR2, CR3, NEF, RAF, ORF, RW2, DNG, JPG e JPEG (incluindo extensões em maiúsculas).

## Extração

A extração sempre copia; nunca move. A categoria é determinística:

- sem `burst_id`: `02_Isoladas`;
- `MELHOR` em uma rajada: `01_Melhores`;
- `ALTERNATIVA` em uma rajada: `03_Boas_Alternativas`;
- `REVISAR` em uma rajada: `04_Revisar`.

Um possível problema técnico não vira automaticamente `REVISAR`. Se já houver
um arquivo idêntico, ele é ignorado. Um arquivo diferente com o mesmo nome é
registrado como conflito e não é sobrescrito.

## Dados

Os trabalhos ficam em:

`%LOCALAPPDATA%\WeddingCulling\WeddingCulling_Data`

Cada execução recebe uma pasta exclusiva com relatório HTML, CSV, JSON,
previews e cache. Preferências e tamanho da janela são persistidos pelo Windows.
O log técnico fica em:

`%LOCALAPPDATA%\WeddingCulling\WeddingCulling.log`

## Segurança

Os arquivos originais são abertos somente para leitura. Relatórios e cache ficam na
pasta de dados do usuário. A única saída envolvendo um original é uma cópia
explícita para o destino escolhido na tela de extração.

Para desenvolvimento e recompilação, consulte `BUILD_WINDOWS.md`.

## Recuperar após reinstalar o Windows

Clone o repositório:

```powershell
git clone https://github.com/FelipedaHora/WeddingCulling.git
cd WeddingCulling
```

O instalador 1.1.0 e seu SHA256 estão em `dist/installer/`. Para desenvolver,
recrie o ambiente seguindo `BUILD_WINDOWS.md`.

O Git não armazena fotografias, relatórios gerados nem os dados em
`%LOCALAPPDATA%\WeddingCulling`. Faça backup separado desses dados antes de
formatar o computador, caso queira preservá-los.

JPEGs usam os mesmos critérios, pesos e limites de seleção dos RAWs. Pastas
mistas são aceitas. A orientação EXIF é respeitada nos JPEGs; sem data EXIF,
é usada a data de modificação do arquivo. A extração copia os bytes originais,
sem recompressão. RAW e JPEG da mesma foto são analisados como arquivos separados.
