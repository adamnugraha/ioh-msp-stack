/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Address of the operator's logo, for a preview build that should show one (the Docker preview image). */
  readonly VITE_BRAND_LOGO?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
