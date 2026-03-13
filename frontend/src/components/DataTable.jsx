import React, { useState } from 'react'
import { Download, AlertTriangle } from 'lucide-react'
import { exportApi, downloadBlob } from '../services/api'

export default function DataTable({ columns, rows, sql, onExport }) {
  const [exporting, setExporting] = useState(false)
  const [page, setPage] = useState(0)
  const pageSize = 25

  const totalPages = Math.ceil(rows.length / pageSize)
  const pageRows = rows.slice(page * pageSize, (page + 1) * pageSize)

  const handleExport = async () => {
    setExporting(true)
    try {
      const r = await exportApi.exportQuery({
        filename: `query_export_${Date.now()}.xlsx`,
        title: 'Résultat de requête',
        columns,
        rows,
        sql,
      })
      downloadBlob(r.data, `query_export_${Date.now()}.xlsx`)
      onExport?.('Export téléchargé', 'success')
    } catch (e) {
      onExport?.('Erreur export: ' + e.message, 'error')
    } finally {
      setExporting(false)
    }
  }

  return (
    <div>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '8px 12px', borderBottom: '1px solid var(--border)',
        background: 'var(--bg-secondary)',
      }}>
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          {rows.length} ligne(s) · {columns.length} colonne(s)
          {rows.length >= 1000 && (
            <span style={{ color: 'var(--warning)', marginLeft: 8 }}>
              <AlertTriangle size={12} style={{ display: 'inline' }} /> Limité à 1000 lignes
            </span>
          )}
        </span>
        <button className="btn btn-secondary btn-sm" onClick={handleExport} disabled={exporting}>
          {exporting ? <div className="spinner" style={{ width: 12, height: 12 }} /> : <Download size={12} />}
          Excel
        </button>
      </div>

      <div style={{ overflowX: 'auto', maxHeight: 320, overflowY: 'auto' }}>
        <table className="data-table" style={{ minWidth: '100%' }}>
          <thead style={{ position: 'sticky', top: 0, zIndex: 1 }}>
            <tr>
              {columns.map((col) => (
                <th key={col}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pageRows.map((row, ri) => (
              <tr key={ri}>
                {row.map((cell, ci) => (
                  <td key={ci} title={String(cell ?? '')}>
                    {cell === null ? <span style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>NULL</span> : String(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
          padding: '8px 12px', borderTop: '1px solid var(--border)',
          fontSize: 12,
        }}>
          <button className="btn btn-secondary btn-sm" onClick={() => setPage(Math.max(0, page - 1))} disabled={page === 0}>←</button>
          <span style={{ color: 'var(--text-muted)' }}>Page {page + 1} / {totalPages}</span>
          <button className="btn btn-secondary btn-sm" onClick={() => setPage(Math.min(totalPages - 1, page + 1))} disabled={page >= totalPages - 1}>→</button>
        </div>
      )}
    </div>
  )
}
