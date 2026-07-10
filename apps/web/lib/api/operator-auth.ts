export function getOperatorAuthHeaders(): Record<string, string> {
  const envToken = process.env.NEXT_PUBLIC_OPERATOR_BEARER_TOKEN;
  if (envToken && envToken.trim().length > 0) {
    return { Authorization: `Bearer ${envToken.trim()}` };
  }

  if (typeof window === "undefined") {
    return {};
  }

  try {
    const token = window.localStorage.getItem("omnitrade.operatorToken");
    if (token && token.trim().length > 0) {
      return { Authorization: `Bearer ${token.trim()}` };
    }
  } catch {
    // LocalStorage access can fail in restricted contexts.
  }

  return {};
}
