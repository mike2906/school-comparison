function SchoolCardSkeleton() {
  return (
    <div className="p-5 max-md:p-4 mx-3 my-3 rounded-xl bg-white border border-neutral-200 shadow-card">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="skeleton h-3 w-3 rounded-full" />
          <div className="skeleton h-5 w-56 rounded" />
        </div>
        <div className="hidden sm:block skeleton h-6 w-20 rounded-full" />
      </div>

      <div className="mt-2 flex gap-2">
        <div className="skeleton h-5 w-16 rounded" />
        <div className="skeleton h-5 w-32 rounded" />
      </div>

      <div className="mt-3 h-px w-full bg-neutral-200" />

      <div className="mt-3 flex gap-2">
        <div className="skeleton h-4 w-4 rounded" />
        <div className="skeleton h-4 w-64 rounded" />
      </div>

      <div className="mt-3 space-y-2">
        <div className="skeleton h-4 w-52 rounded" />
        <div className="skeleton h-4 w-56 rounded" />
        <div className="skeleton h-4 w-48 rounded hidden md:block" />
      </div>

      <div className="mt-3">
        <div className="skeleton h-8 w-full rounded" />
      </div>
    </div>
  )
}

export default SchoolCardSkeleton
