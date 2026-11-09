/** Small marks for the services Vakya works with, drawn inline (no third-party requests). Each names its service for
 *  screen readers. Used on the landing page only. */

export function WhatsAppMark({ className = "size-9" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} role="img" aria-label="WhatsApp">
      <circle cx="16" cy="16" r="16" fill="#25D366" />
      <path
        fill="#fff"
        d="M16 7.2a8.8 8.8 0 0 0-7.6 13.2L7.2 24.8l4.5-1.2A8.8 8.8 0 1 0 16 7.2Zm0 16a7.2 7.2 0 0 1-3.7-1l-.3-.2-2.7.7.7-2.6-.2-.3A7.2 7.2 0 1 1 16 23.2Zm4-5.4c-.2-.1-1.3-.6-1.5-.7-.2-.1-.4-.1-.5.1l-.7.9c-.1.2-.3.2-.5.1a5.9 5.9 0 0 1-2.9-2.6c-.2-.4.2-.4.6-1.2.1-.1 0-.3 0-.4l-.7-1.6c-.2-.4-.4-.4-.5-.4h-.4a.8.8 0 0 0-.6.3 2.5 2.5 0 0 0-.8 1.9 4.4 4.4 0 0 0 .9 2.3 10 10 0 0 0 3.8 3.4c1.4.6 2 .7 2.7.6a2.3 2.3 0 0 0 1.5-1.1 1.9 1.9 0 0 0 .1-1.1c0-.1-.2-.2-.4-.3Z"
      />
    </svg>
  );
}

export function GoogleCalendarMark({ className = "size-9" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} role="img" aria-label="Google Calendar">
      <rect x="3" y="3" width="26" height="26" rx="4" fill="#fff" stroke="#DADCE0" />
      <path d="M3 9a6 6 0 0 1 6-6h14a6 6 0 0 1 6 6H3Z" fill="#4285F4" />
      <rect x="23" y="23" width="6" height="6" rx="1" fill="#EA4335" />
      <rect x="3" y="23" width="6" height="6" rx="1" fill="#34A853" />
      <rect x="23" y="9" width="6" height="14" fill="#FBBC04" opacity=".9" />
      <text x="14" y="23" textAnchor="middle" fontSize="11" fontWeight="700" fill="#1A73E8" fontFamily="Arial, sans-serif">31</text>
    </svg>
  );
}

export function VobizMark({ className = "h-9" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center rounded-lg bg-[#111827] px-2.5 font-semibold tracking-tight text-white ${className}`} role="img" aria-label="Vobiz">
      <svg viewBox="0 0 16 16" className="mr-1.5 size-4" aria-hidden>
        <path fill="#22D3EE" d="M3.6 1.5 6 4.3 4.6 6a8.6 8.6 0 0 0 5.4 5.4l1.7-1.4 2.8 2.4-1.2 2.2c-.3.5-.9.8-1.5.7A12.4 12.4 0 0 1 .7 4.2c-.1-.6.2-1.2.7-1.5Z" />
      </svg>
      vobiz
    </span>
  );
}

export function GnaniMark({ className = "h-9" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center rounded-lg border border-[var(--s-line)] bg-white px-2.5 font-semibold tracking-tight text-[#111827] ${className}`} role="img" aria-label="Gnani AI">
      <span aria-hidden className="mr-1.5 inline-block size-2.5 rounded-full bg-[var(--violet-700,#6D5BD0)]" />
      gnani.ai
    </span>
  );
}
