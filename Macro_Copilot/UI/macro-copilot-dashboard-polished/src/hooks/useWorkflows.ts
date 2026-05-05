// ============================================================================
// useWorkflows / useTools / useWorkflow / useTool
// ----------------------------------------------------------------------------
// Tiny stateful hooks against the workflows + tools REST surface.  No
// external query library — keeps the bundle slim, mirrors the discipline of
// the existing `useRatesData` / `useDashboardData` hooks.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchToolCard,
  fetchTools,
  fetchWorkflowCard,
  fetchWorkflows,
} from '@/services/workflowsApi';
import type { ToolCard, WorkflowTemplateCard } from '@/types/workflows';

type AsyncResult<T> = {
  data: T | null;
  isLoading: boolean;
  error: Error | null;
};

// ---------------------------------------------------------------------------
// Catalogues (lists)
// ---------------------------------------------------------------------------

export function useWorkflows(): AsyncResult<WorkflowTemplateCard[]> {
  const [data, setData] = useState<WorkflowTemplateCard[] | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    fetchWorkflows()
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setIsLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e);
        setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { data, isLoading, error };
}

export function useTools(): AsyncResult<ToolCard[]> {
  const [data, setData] = useState<ToolCard[] | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    fetchTools()
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setIsLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e);
        setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { data, isLoading, error };
}

// ---------------------------------------------------------------------------
// Single-card lookups (used by detail panels)
// ---------------------------------------------------------------------------

export function useWorkflow(templateId: string | null): AsyncResult<WorkflowTemplateCard> {
  const [data, setData] = useState<WorkflowTemplateCard | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(!!templateId);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!templateId) {
      setData(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    fetchWorkflowCard(templateId)
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setIsLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e);
        setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [templateId]);

  return { data, isLoading, error };
}

export function useTool(toolName: string | null): AsyncResult<ToolCard> {
  const [data, setData] = useState<ToolCard | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(!!toolName);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!toolName) {
      setData(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    fetchToolCard(toolName)
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setIsLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e);
        setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [toolName]);

  return { data, isLoading, error };
}
