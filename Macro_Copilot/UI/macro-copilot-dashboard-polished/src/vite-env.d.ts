/// <reference types="vite/client" />

// Project-specific env variables.  Vite exposes any ``VITE_*`` env
// var at build time via ``import.meta.env``; declaring them here
// makes TypeScript aware of the types so consumers don't need
// inline ``as string`` casts.
//
// Add a line per new ``VITE_*`` var as it's introduced.

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  /** PR A — gates the redesigned Build / Workspace surface.  When
   *  set to ``"1"`` (or ``"true"`` / ``"yes"``), AppShell routes
   *  ``/workspace*`` to the new BuildShell.  Default (unset) keeps
   *  the legacy WorkspacePage + WorkspaceBySlugPage mounted. */
  readonly VITE_BUILD_V2?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
