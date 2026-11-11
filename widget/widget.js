/* Sourced website chat. This file is served as a versioned, integrity-pinned script. */
(() => {
  'use strict'
  const script = document.currentScript
  const key = script?.dataset.siteKey
  if (!key || !/^pk_[A-Za-z0-9_-]{32}$/.test(key)) return
  const asset = new URL(script.src)
  const hosted = script.dataset.mode === 'hosted'
  const api = new URL('/api/v1/public/', asset)

  function element(tag, className, text) {
    const node = document.createElement(tag)
    if (className) node.className = className
    if (text !== undefined) node.textContent = text
    return node
  }

  function icon(name) {
    const paths = {
      chat: 'M21 12a8 8 0 0 1-11.6 7.1L4 21l1.9-5.4A8 8 0 1 1 21 12z',
      close: 'M6 6l12 12M18 6 6 18',
      send: 'M5 12h14m-6-6 6 6-6 6',
      file: 'M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6',
      check: 'm5 12 5 5L20 7',
    }
    const ns = 'http://www.w3.org/2000/svg'
    const svg = document.createElementNS(ns, 'svg')
    for (const [attribute, value] of Object.entries({
      viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2',
      'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true',
    })) svg.setAttribute(attribute, value)
    const path = document.createElementNS(ns, 'path')
    path.setAttribute('d', paths[name])
    svg.append(path)
    return svg
  }

  function button(className, label, glyph) {
    const node = element('button', className)
    node.type = 'button'
    node.setAttribute('aria-label', label)
    if (glyph) node.append(icon(glyph))
    return node
  }

  async function request(url, options) {
    const response = await fetch(url, { ...options, credentials: 'omit', redirect: 'error', referrerPolicy: 'no-referrer' })
    if (!response.ok) {
      if (response.status === 429) throw new Error('Too many questions. Please try again later.')
      throw new Error('Chat is unavailable. Please try again later.')
    }
    return response
  }

  function brand(panel, color) {
    if (!/^#[0-9a-f]{6}$/i.test(color)) return
    const channels = [1, 3, 5].map((index) => {
      const value = parseInt(color.slice(index, index + 2), 16) / 255
      return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
    })
    const luminance = channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722
    panel.style.setProperty('--brand', color)
    panel.style.setProperty('--on-brand', luminance > 0.179 ? '#000' : '#fff')
  }

  function start() {
    const host = element('div')
    const root = host.attachShadow({ mode: 'open' })
    const css = element('link')
    css.rel = 'stylesheet'
    css.href = new URL('widget.css', asset).href
    const ui = element('div', hosted ? 'widget hosted' : 'widget')
    ui.hidden = true
    css.addEventListener('load', () => { ui.hidden = false })
    root.append(css, ui)
    document.body.append(host)

    const launcher = button('launcher', 'Open chat', 'chat')
    launcher.setAttribute('aria-expanded', 'false')
    launcher.setAttribute('aria-controls', 'sourced-chat')
    const panel = element('section', 'panel')
    panel.id = 'sourced-chat'
    panel.hidden = !hosted
    panel.setAttribute('aria-labelledby', 'sourced-title')
    const header = element('header', 'header')
    const heading = element('div')
    const title = element('h1', '', 'Chat')
    title.id = 'sourced-title'
    heading.append(title, element('p', '', 'Answers from the documents, with sources'))
    const close = button('close', 'Close chat', 'close')
    header.append(heading)
    if (!hosted) header.append(close)
    const messages = element('div', 'messages')
    messages.setAttribute('role', 'log')
    messages.setAttribute('aria-label', 'Conversation')
    const form = element('form', 'composer')
    form.setAttribute('aria-label', 'Ask a question')
    const input = element('input')
    input.placeholder = 'Ask about policies…'
    input.setAttribute('aria-label', 'Question')
    input.maxLength = 2000
    input.autocomplete = 'off'
    const send = button('send', 'Send', 'send')
    send.type = 'submit'
    send.disabled = true
    form.append(input, send)
    const powered = element('div', 'powered', 'Powered by Sourced')
    const source = element('dialog', 'source')
    source.setAttribute('aria-label', 'Source')
    panel.append(header, messages, form, powered, source)
    ui.append(panel)
    if (!hosted) ui.append(launcher)

    let config
    let pending = false
    let sourceSequence = 0
    let objectUrl = ''
    const history = []
    const message = (text, kind = 'answer') => {
      const node = element('div', 'message ' + kind, text)
      messages.append(node)
      messages.scrollTop = messages.scrollHeight
      return node
    }
    const loading = message('Loading chat…')

    function toggle(open) {
      panel.hidden = !open
      launcher.setAttribute('aria-expanded', String(open))
      launcher.setAttribute('aria-label', open ? 'Close chat launcher' : 'Open chat')
      if (open) input.focus()
      else launcher.focus()
    }
    launcher.addEventListener('click', () => toggle(panel.hidden))
    close.addEventListener('click', () => toggle(false))
    panel.addEventListener('keydown', (event) => {
      if (!hosted && event.key === 'Escape' && !source.open && !event.defaultPrevented) toggle(false)
    })
    input.addEventListener('input', () => { send.disabled = !config || pending || !input.value.trim() })

    request(new URL('widgets/' + key + '/config', api)).then((response) => response.json()).then((value) => {
      if (value.access_mode !== 'public') throw new Error('Chat is unavailable.')
      config = value
      title.textContent = value.title
      brand(ui, value.brand_color)
      loading.remove()
      if (value.greeting) message(value.greeting)
      send.disabled = !input.value.trim()
    }).catch(() => { loading.textContent = 'Chat is unavailable on this website.' })

    function openSource(citation) {
      const sequence = ++sourceSequence
      const info = element('div')
      info.append(
        element('p', 'eyebrow', 'Source ' + citation.number),
        element('h2', '', citation.source),
        element('p', 'detail', 'Page ' + citation.page + (citation.verified ? ' · Quote verified' : '')),
      )
      const dismiss = button('dismiss', 'Close source', 'close')
      dismiss.addEventListener('click', () => source.close())
      const top = element('header', 'source-heading')
      top.append(info, dismiss)
      const status = element('p', 'detail', 'Loading page preview…')
      status.setAttribute('role', 'status')
      source.replaceChildren(top, element('blockquote', '', citation.quote), status)
      source.showModal()
      dismiss.focus()
      const path = citation.preview_url
      if (typeof path !== 'string' || !/^\/api\/v1\/public\/previews\/[A-Za-z0-9_-]+\.[a-f0-9]{64}\.png$/.test(path)) {
        status.textContent = 'Page preview unavailable.'
        return
      }
      request(new URL(path, api)).then((response) => {
        if (response.headers.get('content-type') !== 'image/png') throw new Error('Invalid preview.')
        return response.blob()
      }).then((blob) => {
        if (sequence !== sourceSequence || !source.open) return
        objectUrl = URL.createObjectURL(blob)
        const image = element('img', 'preview')
        image.alt = 'Page ' + citation.page + ' preview'
        image.src = objectUrl
        status.replaceWith(image)
      }).catch(() => { status.textContent = 'Page preview unavailable or expired.' })
    }

    source.addEventListener('close', () => {
      sourceSequence++
      if (objectUrl) URL.revokeObjectURL(objectUrl)
      objectUrl = ''
    })

    function showAnswer(answer) {
      const card = message('')
      if (answer.status !== 'answered') {
        card.append(element('p', '', answer.status === 'not_found'
          ? answer.text : 'I couldn’t verify an answer against the documents, so I’m not showing one.'))
        if (answer.fallback) card.append(element('p', 'detail', answer.fallback))
        messages.scrollTop = messages.scrollHeight
        return
      }
      const text = element('p', 'answer-text')
      const citations = answer.citations
      answer.text.split(/(\[\d{1,2}(?:\s*,\s*\d{1,2})*\])/g).forEach((part, index) => {
        if (index % 2 === 0) text.append(document.createTextNode(part))
        else for (const number of part.slice(1, -1).split(',')) {
          const citation = citations.find((item) => item.number === Number(number))
          if (!citation) text.append(document.createTextNode('[' + number.trim() + ']'))
          else {
            const marker = button('marker', 'Source ' + citation.number)
            marker.textContent = String(citation.number)
            marker.addEventListener('click', () => openSource(citation))
            text.append(marker)
          }
        }
      })
      card.append(text)
      for (const citation of citations) {
        const chip = button('chip', citation.source + ' · page ' + citation.page, 'file')
        chip.append(element('span', '', citation.source.replace(/_/g, ' ') + ' · page ' + citation.page))
        chip.addEventListener('click', () => openSource(citation))
        card.append(chip)
      }
      if (answer.partial) card.append(element('p', 'detail', 'The documents answer part of this question.'))
      if (answer.judgement?.verdict === 'supported') {
        const checked = element('p', 'checked')
        checked.append(icon('check'), document.createTextNode('Checked against the source'))
        card.append(checked)
      }
      messages.scrollTop = messages.scrollHeight
    }

    form.addEventListener('submit', async (event) => {
      event.preventDefault()
      const question = input.value.trim()
      if (!config || !question || pending) return
      pending = true
      send.disabled = true
      input.value = ''
      input.focus()
      const sent = message(question, 'question')
      const searching = message('Searching the documents…')
      try {
        const chat = new URL('chat', api)
        chat.searchParams.set('site_key', key)
        const response = await request(chat, {
          method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Sourced-Key': key },
          body: JSON.stringify({ question, history: history.slice(-6) }),
        })
        const answer = await response.json()
        searching.remove()
        showAnswer(answer)
        history.push(
          { role: 'user', content: question.slice(0, 600) },
          { role: 'assistant', content: (answer.status === 'answered' ? answer.text : 'No answer was shown.').slice(0, 600) },
        )
        if (history.length > 6) history.splice(0, history.length - 6)
      } catch (error) {
        sent.remove()
        searching.textContent = error.message
        if (!input.value) input.value = question
      } finally {
        pending = false
        send.disabled = !input.value.trim()
      }
    })
  }

  if (document.body) start()
  else document.addEventListener('DOMContentLoaded', start, { once: true })
})()
