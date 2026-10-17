import { ExternalLink } from "lucide-react";
import type { ReactNode } from "react";
import { toast } from "sonner";
import { DEMO, type ToolName } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * A link into one of the tools. The tools sign people in through Keycloak, so the page opens signed in.
 * In the preview the tools are not connected: the link says where it would go instead.
 */
export function ToolLink({
  href,
  tool,
  children,
  className,
  icon = false,
  title,
}: {
  href: string | undefined | null;
  tool: ToolName;
  children: ReactNode;
  className?: string;
  icon?: boolean;
  title?: string;
}) {
  if (!href) return <span className={className}>{children}</span>;
  const content = (
    <>
      {children}
      {icon && <ExternalLink aria-hidden className="size-3.5 shrink-0 opacity-70" />}
    </>
  );
  const cls = cn("inline-flex items-center gap-1 text-link underline-offset-2 hover:underline", className);
  if (DEMO) {
    return (
      <button
        type="button"
        title={title ?? `Opens ${tool}`}
        className={cn(cls, "cursor-pointer text-left")}
        onClick={() =>
          toast(`Opens ${tool}, signed in with your Keycloak account`, {
            description: `${href} (the tools are not connected to the preview)`,
          })
        }
      >
        {content}
      </button>
    );
  }
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={cls} title={title ?? `Opens ${tool} in a new tab`}>
      {content}
    </a>
  );
}
