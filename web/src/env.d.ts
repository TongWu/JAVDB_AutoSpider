/// <reference types="vite/client" />

declare global {
  interface Window {
    desktopEnv?: {
      isElectron?: boolean;
      apiBase?: string;
    };
  }
}

export {};
