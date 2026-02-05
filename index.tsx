
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

const root = createRoot(rootElement);
root.render(
  <StrictMode>
    <App />
  </StrictMode>
);
