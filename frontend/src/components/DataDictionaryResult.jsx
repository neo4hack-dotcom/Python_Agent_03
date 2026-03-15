import React, { useState } from 'react'
import { ChevronDown, ChevronUp, Copy, Check, BookOpen, FileText } from 'lucide-react'

// ── Utility: copy to clipboard ───────────────────────────────────────────────
function CopyBtn({ text }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500) }}
      style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '2px 6px', color: copied ? '#10b981' : 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 4, fontSize: 11 }}
      title="Copier"
    >
      {copied ? <Check size={11} /> : <Copy size={11} />}
    </button>
  )
}

// ── Table entry (accordion item) ─────────────────────────────────────────────
function TableAccordion({ entry }) {
  const [open, setOpen] = useState(false)
  const table = entry.table || ''
  const desc = entry.table_description || ''
  const cols = entry.columns || []
  const hasError = !!entry.error && cols.length === 0

  return (
    <div style={{
      border: '1px solid var(--border)', borderRadius: 8,
      overflow: 'hidden', marginBottom: 8,
      background: 'var(--bg-primary)',
    }}>
      {/* Header */}
      <div
        onClick={() => !hasError && setOpen(v => !v)}
        style={{
          padding: '10px 14px', cursor: hasError ? 'default' : 'pointer',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          background: open ? 'rgba(99,102,241,0.07)' : 'var(--bg-card)',
          transition: 'background 0.15s',
        }}
        onMouseEnter={e => { if (!hasError && !open) e.currentTarget.style.background = 'rgba(255,255,255,0.03)' }}
        onMouseLeave={e => { if (!open) e.currentTarget.style.background = 'var(--bg-card)' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <span style={{ fontSize: 13 }}>{hasError ? '⚠️' : '📋'}</span>
          <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 13, color: hasError ? 'var(--warning)' : 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {table}
          </span>
          <span style={{ fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
            {hasError ? '— erreur' : `${cols.length} col.`}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
          {!hasError && <span style={{ fontSize: 11, color: 'var(--text-muted)', maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{desc}</span>}
          {!hasError && (open ? <ChevronUp size={14} color="var(--text-muted)" /> : <ChevronDown size={14} color="var(--text-muted)" />)}
        </div>
      </div>

      {/* Body */}
      {open && !hasError && (
        <div style={{ padding: '12px 14px', borderTop: '1px solid var(--border)' }}>
          {desc && (
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 12, lineHeight: 1.5 }}>{desc}</p>
          )}
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ background: 'rgba(255,255,255,0.03)', borderBottom: '1px solid var(--border)' }}>
                  {['Colonne', 'Type', 'Description', 'Format', 'Valeurs possibles'].map(h => (
                    <th key={h} style={{ padding: '6px 10px', textAlign: 'left', fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', whiteSpace: 'nowrap' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {cols.map((col, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                    <td style={{ padding: '7px 10px', fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 12, color: 'var(--accent)', whiteSpace: 'nowrap' }}>{col.name}</td>
                    <td style={{ padding: '7px 10px', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{col.type}</td>
                    <td style={{ padding: '7px 10px', fontSize: 12, color: 'var(--text-primary)', lineHeight: 1.4, minWidth: 160 }}>{col.business_description}</td>
                    <td style={{ padding: '7px 10px', fontSize: 11, color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>{col.format}</td>
                    <td style={{ padding: '7px 10px', fontSize: 11, color: 'var(--text-secondary)' }}>
                      {col.possible_values?.length
                        ? col.possible_values.map((v, j) => (
                          <span key={j} style={{ display: 'inline-block', background: 'rgba(99,102,241,0.12)', borderRadius: 4, padding: '1px 6px', marginRight: 4, marginBottom: 2, fontSize: 10, fontFamily: 'var(--font-mono)' }}>{v}</span>
                        ))
                        : <span style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>—</span>
                      }
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Tech Spec renderer ────────────────────────────────────────────────────────
function TechSpec({ dictionary }) {
  const now = new Date().toLocaleDateString('fr-FR', { year: 'numeric', month: 'long', day: 'numeric' })

  const buildMarkdown = () => {
    const lines = [
      `# Dictionnaire de Données`,
      ``,
      `**Généré le :** ${now}  `,
      `**Tables documentées :** ${dictionary.length}`,
      ``,
      `---`,
      ``,
      `## Table des matières`,
      ``,
    ]
    dictionary.forEach((entry, i) => {
      lines.push(`${i + 1}. [\`${entry.table}\`](#${entry.table.replace(/[^a-z0-9]/gi, '-').toLowerCase()})`)
    })
    lines.push(``, `---`, ``)
    dictionary.forEach((entry) => {
      const anchor = entry.table.replace(/[^a-z0-9]/gi, '-').toLowerCase()
      lines.push(`## \`${entry.table}\``, ``)
      if (entry.error && !entry.columns?.length) {
        lines.push(`> ⚠️ **Erreur :** ${entry.table_description || entry.error}`, ``)
      } else {
        if (entry.table_description) {
          lines.push(entry.table_description, ``)
        }
        if (entry.columns?.length) {
          lines.push(`| Colonne | Type | Description | Format | Valeurs possibles |`)
          lines.push(`|---------|------|-------------|--------|-------------------|`)
          entry.columns.forEach(col => {
            const pv = col.possible_values?.length ? col.possible_values.join(', ') : '—'
            lines.push(`| \`${col.name}\` | \`${col.type}\` | ${col.business_description || ''} | ${col.format || '—'} | ${pv} |`)
          })
          lines.push(``)
        }
      }
      lines.push(`---`, ``)
    })
    return lines.join('\n')
  }

  const md = buildMarkdown()

  return (
    <div style={{ position: 'relative' }}>
      <div style={{ position: 'absolute', top: 8, right: 8 }}>
        <CopyBtn text={md} />
      </div>
      <pre style={{
        fontFamily: 'var(--font-mono)', fontSize: 12, lineHeight: 1.7,
        color: 'var(--text-primary)', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
        margin: 0, padding: '12px 36px 12px 12px', overflowY: 'auto', maxHeight: 520,
      }}>
        {md}
      </pre>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────
export default function DataDictionaryResult({ dictionary }) {
  const [view, setView] = useState('accordion')  // 'accordion' | 'techspec'

  if (!dictionary || dictionary.length === 0) return null

  const okCount = dictionary.filter(e => !e.error || e.columns?.length > 0).length
  const errCount = dictionary.length - okCount
  const totalCols = dictionary.reduce((s, e) => s + (e.columns?.length || 0), 0)

  return (
    <div style={{
      marginTop: 4, border: '1px solid rgba(99,102,241,0.3)',
      borderRadius: 10, overflow: 'hidden',
      background: 'rgba(99,102,241,0.03)',
    }}>
      {/* Toolbar */}
      <div style={{
        padding: '8px 12px', borderBottom: '1px solid rgba(99,102,241,0.2)',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        background: 'rgba(99,102,241,0.07)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
          <span style={{ fontWeight: 700, color: '#6366f1' }}>📖 Dictionnaire de Données</span>
          <span style={{ color: 'var(--text-muted)' }}>·</span>
          <span style={{ color: 'var(--text-muted)' }}>{dictionary.length} tables</span>
          <span style={{ color: 'var(--text-muted)' }}>·</span>
          <span style={{ color: 'var(--text-muted)' }}>{totalCols} colonnes</span>
          {errCount > 0 && <span style={{ color: 'var(--warning)', fontSize: 11 }}>· {errCount} erreur(s)</span>}
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          {[
            { key: 'accordion', icon: <BookOpen size={12} />, label: 'Accordéon' },
            { key: 'techspec',  icon: <FileText size={12} />,  label: 'Tech Spec' },
          ].map(v => (
            <button
              key={v.key}
              onClick={() => setView(v.key)}
              style={{
                display: 'flex', alignItems: 'center', gap: 5, padding: '3px 10px',
                background: view === v.key ? 'rgba(99,102,241,0.25)' : 'transparent',
                border: `1px solid ${view === v.key ? 'var(--accent)' : 'var(--border)'}`,
                borderRadius: 6, cursor: 'pointer', fontSize: 11,
                color: view === v.key ? 'var(--accent)' : 'var(--text-muted)',
                fontWeight: view === v.key ? 700 : 400,
              }}
            >
              {v.icon}{v.label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div style={{ padding: view === 'techspec' ? 0 : 12, maxHeight: 600, overflowY: 'auto' }}>
        {view === 'accordion'
          ? dictionary.map((entry, i) => <TableAccordion key={i} entry={entry} />)
          : <TechSpec dictionary={dictionary} />
        }
      </div>
    </div>
  )
}
