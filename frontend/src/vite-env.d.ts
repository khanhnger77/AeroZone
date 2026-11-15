/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Hosted backend serving /api and /sim (static deployments only); unset = same origin. */
  readonly VITE_BACKEND_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
