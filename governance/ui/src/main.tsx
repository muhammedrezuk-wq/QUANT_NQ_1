import React from 'react'
import ReactDOM from 'react-dom/client'
import '@fontsource/ibm-plex-sans-arabic/400.css'
import '@fontsource/ibm-plex-sans-arabic/500.css'
import '@fontsource/ibm-plex-sans-arabic/600.css'
import '@fontsource/ibm-plex-sans-arabic/700.css'
import App from './App'
import './styles.css'
import { bootstrapAuth } from './core/auth'
import { initAppearance } from './core/appearance'

// تخصيص الشكل المحفوظ (ألوان · تكبير) يُطبَّق قبل أوّل رسم — بلا وميض افتراضي
initAppearance()

async function boot(): Promise<void> {
  const root = ReactDOM.createRoot(document.getElementById('root')!)
  if (!await bootstrapAuth()) {
    root.render(<div style={{ padding: 32, direction: 'rtl' }}>تعذّر توثيق واجهة الحوكمة.</div>)
    return
  }
  root.render(<React.StrictMode><App /></React.StrictMode>)
}
void boot()
