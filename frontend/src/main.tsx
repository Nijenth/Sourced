import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import '@fontsource-variable/inter/wght.css'
import '@fontsource/space-grotesk/600.css'
import './styles.css'
import { App } from './App'

createRoot(document.getElementById('root')!).render(<BrowserRouter><App /></BrowserRouter>)
