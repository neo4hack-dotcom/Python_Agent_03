import React, { useEffect } from 'react'

export function Toast({ message, type = 'info', onClose, duration = 3500 }) {
  useEffect(() => {
    const t = setTimeout(onClose, duration)
    return () => clearTimeout(t)
  }, [onClose, duration])

  return (
    <div className={`toast toast-${type}`} onClick={onClose}>
      {message}
    </div>
  )
}

export function useToast() {
  const [toasts, setToasts] = React.useState([])

  const show = (message, type = 'info') => {
    const id = Date.now()
    setToasts((prev) => [...prev, { id, message, type }])
  }

  const remove = (id) => setToasts((prev) => prev.filter((t) => t.id !== id))

  const ToastContainer = () => (
    <div style={{ position: 'fixed', bottom: 20, right: 20, zIndex: 9999, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {toasts.map((t) => (
        <Toast key={t.id} message={t.message} type={t.type} onClose={() => remove(t.id)} />
      ))}
    </div>
  )

  return { show, ToastContainer }
}
