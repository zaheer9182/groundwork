# Deploying to Azure

Target: Azure Functions Flex Consumption, Linux, Python 3.11, with Key Vault, Application Insights and GitHub Actions using OIDC. Run once per environment.

```bash
RG=rg-groundwork-prod
LOC=eastus2
APP=func-groundwork-prod
ST=stgroundwork$RANDOM
KV=kv-groundwork-prod
AI=appi-groundwork-prod
CLIENT_ORIGIN=https://knowledge.contoso.com
```

## 1. Resources

```bash
az group create -n $RG -l $LOC
az storage account create -n $ST -g $RG -l $LOC --sku Standard_LRS --allow-shared-key-access false
az monitor app-insights component create -a $AI -g $RG -l $LOC
az keyvault create -n $KV -g $RG -l $LOC --enable-rbac-authorization true

az functionapp create -n $APP -g $RG --storage-account $ST \
  --flexconsumption-location $LOC --runtime python --runtime-version 3.11 \
  --app-insights $AI --assign-identity [system]
```

## 2. Managed identity

The Function reads secrets and its own storage with its managed identity, never with keys.

```bash
MI=$(az functionapp identity show -n $APP -g $RG --query principalId -o tsv)
az role assignment create --assignee $MI --role "Key Vault Secrets User" --scope $(az keyvault show -n $KV --query id -o tsv)
az role assignment create --assignee $MI --role "Storage Blob Data Owner" --scope $(az storage account show -n $ST -g $RG --query id -o tsv)
```

Using Azure OpenAI instead of Claude? Also grant `Cognitive Services OpenAI User` on the Azure OpenAI resource and leave `AZURE_OPENAI_API_KEY` unset; the provider authenticates with the managed identity.

## 3. Secrets as Key Vault references

```bash
az keyvault secret set --vault-name $KV -n api-client-secret --value "<from the API app registration>"
az keyvault secret set --vault-name $KV -n anthropic-api-key --value "<from the Anthropic console>"

az functionapp config appsettings set -n $APP -g $RG --settings \
  PYTHON_ENABLE_INIT_INDEXING=1 \
  GW_SOURCE=graph \
  GW_TENANT_ID=<tenant-id> \
  GW_API_CLIENT_ID=<api-client-id> \
  GW_PROVIDER=anthropic \
  GW_MODEL=claude-sonnet-5-5 \
  "GW_API_CLIENT_SECRET=@Microsoft.KeyVault(VaultName=$KV;SecretName=api-client-secret)" \
  "ANTHROPIC_API_KEY=@Microsoft.KeyVault(VaultName=$KV;SecretName=anthropic-api-key)"
```

In the portal, Function App → Environment variables should show a green tick on both Key Vault settings. A red status usually means the role assignment is still propagating (give it a few minutes) or the vault firewall is blocking the app.

**Rotation:** add a new secret version in Key Vault. The app picks it up within 24 hours, or straight away after a restart. No deployment needed. `GW_DEV_MODE` must never be set in Azure.

## 4. Cold starts, CORS

```bash
az functionapp scale config always-ready set -n $APP -g $RG --settings http=1
az functionapp cors add -n $APP -g $RG --allowed-origins $CLIENT_ORIGIN
```

One always-ready instance removes the cold start on the first question of the morning and keeps the chunk cache warm.

## 5. GitHub Actions with OIDC

1. Create a user-assigned identity (or app registration) for deployments and grant it `Website Contributor` on the resource group only.
2. Add federated credentials with issuer `https://token.actions.githubusercontent.com` and subjects `repo:<org>/<repo>:environment:staging` and `repo:<org>/<repo>:environment:production`.
3. Set repository variables: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `FUNCTION_APP_NAME`, `FUNCTION_APP_HOST` (per environment).
4. Add required reviewers to the `production` environment.

`.github/workflows/deploy.yml` deploys on a `v*` tag: staging, health check, then production after approval. `remote-build` builds the Linux wheels on Azure. There is no publish profile and no Azure secret stored in GitHub.

## 6. Check streaming end to end

```bash
curl -N -X POST https://$APP.azurewebsites.net/api/ask \
  -H "Authorization: Bearer <user token for api://<api-client-id>/access_as_user>" \
  -H "Content-Type: application/json" \
  -d '{"question":"Who approves a 1,200 dollar expense claim?"}'
```

`-N` turns off curl's buffering. Status events should appear at once and `delta` events word by word. If the whole answer lands at once, something in front of the Function is buffering: API Management (set `buffer-response="false"` on the forward-request policy), Front Door, or a corporate proxy.

## 7. Monitoring

```kusto
traces
| where message == "groundwork.answer"
| extend d = customDimensions
| summarize questions = count(),
            refused = countif(tobool(d.refused)),
            ungrounded = countif(tobool(d.grounded) == false),
            invented_citations = sum(toint(d.invented_citations)),
            p95_ms = percentile(toint(d.latency_ms), 95)
  by bin(timestamp, 1d)
```

```kusto
traces
| where message == "groundwork.feedback"
| extend d = customDimensions
| summarize helpful = countif(tobool(d.helpful)), not_helpful = countif(tobool(d.helpful) == false) by bin(timestamp, 7d)
```

Questions are redacted (emails, phone and card numbers) before logging, and users appear only as a 16-character hash.
