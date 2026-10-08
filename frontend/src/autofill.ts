// Configuration and assistant inputs are not account credentials.
// Extension hints complement the browser autocomplete policy.
export const nonCredentialInput = {
  autoComplete: "off",
  "data-1p-ignore": "true",
  "data-op-ignore": "true",
  "data-lpignore": "true",
  "data-bwignore": "true",
} as const;
