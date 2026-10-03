# Build para Windows

Requisitos: Windows 10/11 64 bits, Python 3.12 64 bits e Inno Setup 6.
Compilador oficial: https://jrsoftware.org/isdl.php

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\build_windows.ps1
# Outra versao ou local do compilador:
.\build_windows.ps1 -Version 1.0.1 -IsccPath 'C:\caminho\ISCC.exe'
```

O script executa os testes, empacota o aplicativo, executa seu autoteste e
compila `dist\installer\WeddingCulling-Setup-1.1.0-x64.exe`, acompanhado do SHA256.
O compilador pode estar em `.tools\InnoSetup`, Program Files (x86) ou
LocalAppData\Programs\Inno Setup 6.

Distribua somente o instalador EXE. O computador de destino nao precisa de
Python. A instalacao em portugues e feita por usuario, sem administrador,
com atalho no menu Iniciar e atalho opcional na area de trabalho.
A desinstalacao fica disponivel nas configuracoes de aplicativos do Windows.
Os relatorios e preferencias do usuario sao preservados na desinstalacao.
O pacote nao possui assinatura digital.

A pasta `dist\WeddingCulling` tambem pode ser usada como distribuicao portatil;
nesse caso, entregue a pasta inteira, incluindo `_internal`.
A distribuicao one-folder permite carregar as DLLs de Qt, OpenCV, LibRaw e Exiv2.

O autoteste tambem gera um JPEG temporario e valida sua contagem, analise,
relatorio e copia integral. Nenhuma fotografia do usuario e usada nesse teste.

Para validar as dependencias nativas e o fluxo JPEG do binario:

```powershell
$p = Start-Process .\dist\WeddingCulling\WeddingCulling.exe -ArgumentList '--self-test' -Wait -PassThru
$p.ExitCode # 0 indica sucesso
```
