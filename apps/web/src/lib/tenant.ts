/** Tenant courant = premier label du sous-domaine (miroir de `extract_slug` côté API).
 * `null` en dev local (localhost, IP) : aucun tenant n'est adressable sans sous-domaine. */
export function currentTenantSlug(hostname = window.location.hostname): string | null {
  const labels = hostname.split(".");
  if (labels.length < 2) return null;
  const candidate = labels[0] ?? "";
  return /^[a-z][a-z0-9-]{1,38}$/.test(candidate) ? candidate : null;
}
