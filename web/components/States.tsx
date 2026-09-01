import { Icon } from "./Icon";

export function EmptyState({
  title,
  body,
}: {
  title: string;
  body: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-outline px-8 py-16 text-center">
      <Icon name="inbox" className="mb-3 text-[40px] text-outline" />
      <h3 className="font-headline-md text-headline-md text-on-surface">{title}</h3>
      <p className="mt-2 max-w-md font-body-md text-body-md text-on-surface-variant">{body}</p>
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-error-container bg-error-container/40 px-8 py-12 text-center">
      <Icon name="error" className="mb-3 text-[32px] text-error" />
      <h3 className="font-headline-md text-headline-md text-on-surface">Could not load this view</h3>
      <p className="mt-2 max-w-md font-body-md text-body-md text-on-surface-variant">{message}</p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="focus-ring mt-4 rounded-md border border-hairline bg-level-1 px-4 py-2 font-label-md text-label-md text-on-surface"
        >
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-surface-container-high ${className ?? "h-4"}`} />;
}

export function PageSkeleton() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-[120px] rounded-lg" />
        ))}
      </div>
      <Skeleton className="h-[360px] rounded-lg" />
      <Skeleton className="h-48 rounded-lg" />
    </div>
  );
}
