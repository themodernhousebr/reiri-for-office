# Daikin - Reiri for Office — Home Assistant

Integração customizada (v0.1.3) para controladores Daikin/Reiri DCPF01 na rede local.

## Instalação

### HACS (repositório personalizado)

1. Publique este diretório em um repositório GitHub.
2. No HACS, adicione o repositório como **Integration**.
3. Instale **Reiri for Office** e reinicie o Home Assistant.
4. Em **Configurações → Dispositivos e serviços**, adicione **Reiri for Office**.

Também é possível copiar `custom_components/reiri_for_office` para `/config/custom_components/`.

## Configuração

Informe host (IP local), porta (padrão `52001`), usuário e senha locais do Reiri. Uma única entrada representa o DCPF01; cada ponto `type="Ac"` e `usage="ac"` vira um dispositivo e uma entidade `climate`.

Em **Configurar**, a política das unidades slave pode ser:

- `conservative` (padrão): com a master em C, oferece off/cool/dry/fan; em H, off/heat/fan; em F, off/fan.
- `all_reported`: oferece os modos anunciados em `mode_cap` pela própria unidade.

Importante: a regra conservadora das slaves é uma inferência técnica ainda não validada presencialmente. A integração nunca troca automaticamente a master para satisfazer um comando enviado a uma slave. Um modo incompatível é recusado localmente.

## Recursos

- Liga/desliga (`stat`)
- Modos F/C/H/D
- Temperatura ambiente (`temp`)
- Setpoint lido por `csp`/`hsp` e escrito obrigatoriamente como `sp`
- Ventilador (`fanstep`)
- Defletor (`flap`), posições numéricas e `S` para swing
- Atualização em tempo real por COS e reconexão automática
- Confirmação de escrita por `op=OK` e COS, com `mplist` como fallback

O envio de `flap` aplica debounce de 1,2 s, preservando o comportamento confirmado nos testes reais: `0 → 1 → 0` e `0 → S → 0`.

## Observações

O protocolo usa WebSocket local, RSA 2048, RSA OAEP-SHA1 (com fallback PKCS#1 v1.5) e AES-128-CBC com a mesma chave de 16 bytes como chave e IV. Credenciais ficam na config entry do Home Assistant e não são registradas em log.

Esta é uma versão inicial para teste controlado no equipamento real. Faça primeiro alterações não críticas e mantenha acesso ao aplicativo oficial Reiri.
