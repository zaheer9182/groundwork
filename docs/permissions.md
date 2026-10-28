# App registrations and permissions

Two registrations: one for the API (this Function), one for each client.

## API registration

| Setting | Value |
|---|---|
| Supported account types | Single tenant |
| Expose an API | Application ID URI `api://<api-client-id>`, scope `access_as_user` (admins and users can consent) |
| Authorized client applications | The client app id, pre-authorized for `access_as_user` |
| Manifest | `"requestedAccessTokenVersion": 2` |
| Certificates & secrets | Certificate preferred in production; a client secret is fine for staging. Either way the value lives in Key Vault. |
| API permissions | Microsoft Graph, **delegated**: `Files.Read.All`, `Sites.Read.All`. Grant admin consent. |
| `knownClientApplications` | The client app id, so users consent to the client and the API in one prompt |

Do not add any **application** permissions to Graph. The design depends on every Graph call running as the user.

### Why these scopes

| Delegated scope | Used for |
|---|---|
| `Files.Read.All` | `POST /search/query` over `driveItem`, and `GET /drives/{id}/items/{id}/content` |
| `Sites.Read.All` | Searching items in SharePoint sites the user can read; needed when search is scoped with `path:` to sites |

Both are bounded by what the user can already open. Neither grants access to anything the user cannot see in SharePoint or OneDrive. If a deployment only searches the user's own OneDrive, `Files.Read` is enough and `Sites.Read.All` can be dropped.

`User.Read` stays on the client registration for sign-in and profile; the API does not need it.

## Client registration (single-page app)

| Setting | Value |
|---|---|
| Platform | Single-page application, one redirect URI per environment |
| API permissions | `api://<api-client-id>/access_as_user` (delegated) |

The client requests only the API scope. It never holds a Graph token for search; the Function obtains that on-behalf-of the user.

## Conditional access and MFA

If a conditional access policy applies to Graph but the user signed in without satisfying it, the on-behalf-of call fails with `interaction_required` and a claims challenge. The API returns 401 with `WWW-Authenticate: Bearer error="interaction_required"`; the client calls `acquireTokenPopup` (or redirect) and retries. `examples/web/index.html` shows this.

## Checking a token

Decode a client token at https://jwt.ms and confirm:

- `aud` is `api://<api-client-id>` (or the bare id)
- `scp` contains `access_as_user`
- `tid` is the tenant
- `ver` is `2.0`
