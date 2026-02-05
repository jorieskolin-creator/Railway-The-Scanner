
// App and ReactDOM are global
const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error("Could not find root element to mount to");
}

const React = window.React;
const ReactDOM = (window as any).ReactDOM;

if (!ReactDOM) {
    throw new Error("Global ReactDOM is undefined. Check index.html imports.");
}

const { createRoot } = ReactDOM;
const { StrictMode } = React;
const App = (window as any).App;

if (!createRoot) {
     throw new Error("createRoot is undefined in ReactDOM.");
}

if (!App) {
    console.error("CRITICAL: App component not found. It implies App.tsx failed to execute.");
    rootElement.innerHTML = '<div style="color: #ef4444; padding: 20px; font-family: monospace;">Fatal Error: App component failed to load. Check console for syntax errors.</div>';
} else {
    const root = createRoot(rootElement);
    root.render(
      <StrictMode>
        <App />
      </StrictMode>
    );
}
