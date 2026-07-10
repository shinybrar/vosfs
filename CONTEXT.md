# vosfs

This context defines the project-specific language used by the `vosfs`
capability contract and implementation backlog.

## Language

**OpenCADC VOSpace profile**:
The VOSpace behavior implemented by the pinned `opencadc/vos` Cavern source
and tests that bounds the `vosfs` v0.3.0 contract.
_Avoid_: Full VOSpace 2.1 conformance, generic VOSpace support

**Native capability**:
A `vosfs` behavior backed by one implemented and tested operation in the
OpenCADC VOSpace profile.
_Avoid_: Spec-mandated capability

**Client-derived capability**:
A supported `vosfs` behavior composed only from native capabilities, including
documented client-side fallbacks.
_Avoid_: Server capability, native fallback

**Extension-conditional capability**:
A wired OpenCADC extension that `vosfs` exposes only when the deployed service
advertises it and the required behavior is verified.
_Avoid_: Portable VOSpace capability, guaranteed extension

**Unsupported capability**:
A behavior deliberately excluded from v0.3.0 because the OpenCADC VOSpace
profile lacks the required semantics, no approved client-derived behavior
supplies them, or the behavior is outside the v0.3.0 product scope.
_Avoid_: Unimplemented endpoint

**Negotiated byte endpoint**:
The temporary or pre-authorized byte URL returned by `/synctrans` for one
`pullFromVoSpace` read or `pushToVoSpace` write.
_Avoid_: Files URL, configured byte endpoint

**Service base URL**:
The caller-supplied HTTP URL identifying one OpenCADC VOSpace deployment, such
as `https://example.invalid/arc`.
_Avoid_: VOSpace authority, `vos://` authority

**Filesystem path**:
The complete path after the `vos://` protocol marker; its first component does
not select a service or VOSpace authority.
_Avoid_: VOSURI, service-qualified URL

**VOSpace authority**:
The logical authority carried by server-returned VOSURIs and required inside
VOSpace XML documents.
_Avoid_: Service base URL, fsspec URL authority

**tokenfile**:
The public `vosfs` option naming a file whose bearer token is reread before
each authenticated request.
_Avoid_: `token_file`, token provider

**certfile**:
The public `vosfs` option naming a combined X.509 certificate-chain and
private-key PEM file.
_Avoid_: `cert_path`, `key_path`, SSL context

**Whole-object staged read**:
A read that downloads one complete remote object into a disk-backed temporary
file before exposing local seek and range behavior.
_Avoid_: Ranged read, block-cached read

**Staged write**:
A buffered file write committed by one whole-object PUT when the file closes
successfully.
_Avoid_: Multipart upload, resumable upload, atomic write

**Recursive removal**:
A deletion explicitly requested with `recursive=True` and implemented by
client-side traversal followed by leaves-first node DELETE requests.
_Avoid_: `/async-delete`, implicit container deletion

**Synchronous transfer negotiation**:
One `/synctrans` POST followed through its 303 result to obtain a negotiated
byte endpoint, without starting, polling, or aborting an asynchronous job.
_Avoid_: Async transfer job, direct files URL construction

**Internal LinkNode**:
A VOSpace link whose target has the same discovered VOSpace authority and can
be resolved by the OpenCADC service without cross-service I/O.
_Avoid_: Filesystem alias, external link

**Service binding**:
An operation URL and security method advertised by the deployment's VOSI
capabilities document for one approved OpenCADC operation.
_Avoid_: Probed endpoint, guessed resource path

**VOSpaceError**:
The single public `OSError` subclass for OpenCADC failures that have no precise
standard Python filesystem exception.
_Avoid_: HTTP error, UWS error
