const PROVIDER_ERROR_KEYS = new Set([
  "credentials_missing",
  "credentials_invalid",
  "model_missing",
  "model_unavailable",
  "provider_unsupported",
  "quota_exhausted",
  "rate_limited",
  "network_error",
  "response_invalid",
  "provider_error",
]);

export function providerErrorCode(value, explicitCode = "") {
  const explicit = String(explicitCode || "").trim().toLowerCase();
  if (PROVIDER_ERROR_KEYS.has(explicit)) return explicit;
  const text = String(value || "");
  const marker = text.match(/\[([a-z_]+)\]/i)?.[1]?.toLowerCase() || "";
  if (PROVIDER_ERROR_KEYS.has(marker)) return marker;
  const lower = text.toLowerCase();
  if (/user not found|invalid[_ ]api[_ ]key|unauthori[sz]ed|http 40[13]/.test(lower)) {
    return "credentials_invalid";
  }
  if (/insufficient_quota|quota|billing|credit/.test(lower) && /429|quota|billing|credit/.test(lower)) {
    return "quota_exhausted";
  }
  if (/rate[_ ]limit|too many requests|http 429/.test(lower)) return "rate_limited";
  if (/llm_model is not configured|cover_model is not configured/.test(lower)) return "model_missing";
  if (/model[_ ]not[_ ]found|unsupported model|model does not exist|http 404/.test(lower)) {
    return "model_unavailable";
  }
  if (/api key.*not configured|credentials.*missing/.test(lower)) return "credentials_missing";
  if (/network|dns|timed? out|connection/.test(lower)) return "network_error";
  return "";
}

export function providerErrorMessageKey(value, explicitCode = "") {
  const code = providerErrorCode(value, explicitCode);
  return code ? `ai.error.${code}` : "";
}
