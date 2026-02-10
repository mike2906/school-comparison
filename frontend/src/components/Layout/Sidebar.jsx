function Sidebar({ children }) {
  return (
    <aside className="w-[420px] bg-white border-r border-neutral-200 flex flex-col shadow-panel z-10">
      {children}
    </aside>
  )
}

export default Sidebar
