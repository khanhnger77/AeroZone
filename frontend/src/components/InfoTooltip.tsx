import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Info } from 'lucide-react'

/** Portaled so a scrollable sidebar cannot clip the explanation. */
export default function InfoTooltip({ label, children }: { label: string; children: ReactNode }) {
  const id = useId()
  const anchor = useRef<HTMLButtonElement>(null)
  const content = useRef<HTMLDivElement>(null)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null)
  const show = () => { clearTimeout(closeTimer.current); setOpen(true) }
  const hideSoon = () => { clearTimeout(closeTimer.current); closeTimer.current = setTimeout(() => { if (document.activeElement !== anchor.current) { setOpen(false); setPosition(null) } }, 150) }
  useEffect(() => () => clearTimeout(closeTimer.current), [])
  useLayoutEffect(() => {
    if (!open || !anchor.current || !content.current) return
    const button = anchor.current.getBoundingClientRect(), box = content.current.getBoundingClientRect()
    const left = button.right + 10 + box.width <= window.innerWidth - 12 ? button.right + 10 : Math.max(12, button.left - box.width - 10)
    const top = Math.max(12, Math.min(button.top, window.innerHeight - box.height - 12))
    setPosition({ left, top })
  }, [open])
  useEffect(() => {
    if (!open) return
    const dismiss = () => { setOpen(false); setPosition(null) }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.stopPropagation(); dismiss() } }
    const scroll = (event: Event) => { if (!content.current?.contains(event.target as Node)) dismiss() }
    window.addEventListener('keydown', escape, true)
    window.addEventListener('resize', dismiss)
    window.addEventListener('scroll', scroll, true)
    return () => { window.removeEventListener('keydown', escape, true); window.removeEventListener('resize', dismiss); window.removeEventListener('scroll', scroll, true) }
  }, [open])
  return <>
    <button ref={anchor} type="button" aria-label={label} aria-describedby={open ? id : undefined}
      onMouseEnter={show} onMouseLeave={hideSoon} onFocus={show} onBlur={hideSoon}
      onClick={show}
      className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-muted transition-colors hover:bg-cyan/10 hover:text-cyan focus-visible:outline-2 focus-visible:outline-cyan">
      <Info size={16} aria-hidden="true" />
    </button>
    {open && createPortal(<div ref={content} id={id} role="tooltip" onMouseEnter={show} onMouseLeave={hideSoon}
      className="fixed z-[3000] max-h-[calc(100vh-24px)] w-[340px] max-w-[calc(100vw-24px)] overflow-y-auto rounded-xl border border-line-2 bg-panel p-4 text-[11px] leading-relaxed text-muted shadow-2xl"
      style={{ left: position?.left ?? 0, top: position?.top ?? 0, visibility: position ? 'visible' : 'hidden' }}>
      {children}
    </div>, document.body)}
  </>
}
