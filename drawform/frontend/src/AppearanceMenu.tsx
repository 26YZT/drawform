import { ArrowUpRight, X } from 'lucide-react'
import { type AppearanceMode } from './appearance'
import { workflowMenus } from './content'

export default function AppearanceMenu({ mode, waiting, onClose }: {
  mode: AppearanceMode; waiting: boolean; onClose: () => void
}) {
  const current = workflowMenus[mode]
  return <div className={`appearance-menu workflow-dock ${mode}`} id={`appearance-${mode}`} role="group" aria-label={current.label}>
    <div className="appearance-heading"><span>{current.label}</span><button className="appearance-close" onClick={onClose} aria-label={`关闭${current.label}菜单`}><X size={16} strokeWidth={1.5}/></button></div>
    <p className="workflow-dock-description">{current.description}</p>
    <a className="workflow-cta" href={current.href} aria-disabled={waiting || undefined} tabIndex={waiting ? -1 : 0} onClick={event => { if (waiting) event.preventDefault() }}>{current.cta}<ArrowUpRight size={17}/></a>
    <div className="appearance-caption" aria-live="polite">{waiting ? '正在收起交互示意，请稍候…' : current.note}</div>
  </div>
}
