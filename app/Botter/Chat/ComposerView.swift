import AppKit
import SwiftUI
import UniformTypeIdentifiers
import BotterKit

struct ComposerView: View {
    let bot: Bot
    let chat: ChatStore

    @State private var draft = ""
    @State private var pendingImage: PendingImage?
    @State private var isChoosingImage = false
    @State private var attachmentError: String?
    @FocusState private var focused: Bool

    private var canSend: Bool {
        (!draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || pendingImage != nil)
            && !chat.isStreaming
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let pendingImage {
                PendingImagePreview(image: pendingImage) {
                    self.pendingImage = nil
                }
                .transition(.scale(scale: 0.94, anchor: .bottomLeading).combined(with: .opacity))
            }

            HStack(alignment: .center, spacing: 12) {
                Button {
                    isChoosingImage = true
                } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(Tokens.textSecondary)
                        .frame(width: 24, height: 24)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(chat.isStreaming)
                .help(pendingImage == nil ? "Attach image" : "Replace image")

                TextField("Message \(bot.displayName)", text: $draft, axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(Tokens.chatBody)
                    .lineSpacing(3)
                    .foregroundStyle(Tokens.textPrimary)
                    .lineLimit(1...6)
                    .focused($focused)
                    .onSubmit(send)
                    .padding(.vertical, 2)

                trailingControl
            }
        }
        .padding(.leading, 16)
        .padding(.trailing, 14)
        .padding(.vertical, 10)
        .background(
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .fill(Tokens.cardBackground)
                .shadow(color: .black.opacity(0.3), radius: 10, y: 3)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .strokeBorder(
                    focused ? Color.white.opacity(0.14) : Tokens.hairline,
                    lineWidth: 1
                )
        )
        .animation(.easeOut(duration: 0.15), value: focused)
        .animation(.easeOut(duration: 0.18), value: canSend)
        .animation(.easeOut(duration: 0.18), value: chat.isStreaming)
        .frame(maxWidth: 760)
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 24)
        .padding(.vertical, 12)
        .background(Tokens.windowBackground)
        .fileImporter(
            isPresented: $isChoosingImage,
            allowedContentTypes: [.png, .jpeg, .gif, .webP],
            allowsMultipleSelection: false
        ) { result in
            chooseImage(from: result)
        }
        .alert(
            "Couldn’t Attach Image",
            isPresented: Binding(
                get: { attachmentError != nil },
                set: { if !$0 { attachmentError = nil } }
            )
        ) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(attachmentError ?? "The selected image could not be read.")
        }
        .onAppear { focused = true }
    }

    @ViewBuilder
    private var trailingControl: some View {
        if chat.isStreaming {
            Button {
                chat.stop()
            } label: {
                ZStack {
                    Circle().fill(Color.white)
                    Image(systemName: "square.fill")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(.black)
                }
                .frame(width: 28, height: 28)
            }
            .buttonStyle(.pressable)
            .help("Stop")
            .transition(.scale(scale: 0.6).combined(with: .opacity))
        } else if canSend {
            Button(action: send) {
                ZStack {
                    Circle().fill(Color.white)
                    Image(systemName: "arrow.up")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(.black)
                }
                .frame(width: 28, height: 28)
            }
            .buttonStyle(.pressable)
            .help("Send")
            .transition(.scale(scale: 0.6).combined(with: .opacity))
        } else {
            Image(systemName: "mic")
                .font(.system(size: 13))
                .foregroundStyle(Tokens.textSecondary)
                .frame(width: 28, height: 28)
                .help("Dictation: press the microphone key")
                .transition(.opacity)
        }
    }

    private func send() {
        guard canSend else { return }
        let text = draft
        let images = pendingImage.map { [$0.attachment] } ?? []
        draft = ""
        pendingImage = nil
        chat.send(text, images: images)
    }

    private func chooseImage(from result: Result<[URL], Error>) {
        do {
            guard let url = try result.get().first else { return }
            let didAccess = url.startAccessingSecurityScopedResource()
            defer { if didAccess { url.stopAccessingSecurityScopedResource() } }

            let values = try url.resourceValues(forKeys: [.fileSizeKey])
            if let fileSize = values.fileSize, fileSize > PendingImage.maximumBytes {
                throw AttachmentError.tooLarge
            }
            let data = try Data(contentsOf: url, options: .mappedIfSafe)
            guard !data.isEmpty, data.count <= PendingImage.maximumBytes else {
                throw AttachmentError.tooLarge
            }
            guard NSImage(data: data) != nil, let mediaType = PendingImage.mediaType(for: url) else {
                throw AttachmentError.unsupported
            }
            withAnimation(.easeOut(duration: 0.16)) {
                pendingImage = PendingImage(filename: url.lastPathComponent, mediaType: mediaType, data: data)
            }
            focused = true
        } catch {
            attachmentError = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        }
    }
}

private struct PendingImage: Equatable {
    static let maximumBytes = 5_000_000

    let filename: String
    let mediaType: String
    let data: Data

    var attachment: ImageAttachment {
        ImageAttachment(
            url: "data:\(mediaType);base64,\(data.base64EncodedString())",
            mediaType: mediaType,
            filename: filename
        )
    }

    var nsImage: NSImage? { NSImage(data: data) }

    static func mediaType(for url: URL) -> String? {
        switch url.pathExtension.lowercased() {
        case "png": "image/png"
        case "jpg", "jpeg": "image/jpeg"
        case "gif": "image/gif"
        case "webp": "image/webp"
        default: nil
        }
    }
}

private enum AttachmentError: LocalizedError {
    case tooLarge
    case unsupported

    var errorDescription: String? {
        switch self {
        case .tooLarge: "Choose an image smaller than 5 MB."
        case .unsupported: "Choose a PNG, JPEG, GIF, or WebP image."
        }
    }
}

private struct PendingImagePreview: View {
    let image: PendingImage
    let remove: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            if let nsImage = image.nsImage {
                Image(nsImage: nsImage)
                    .resizable()
                    .scaledToFill()
                    .frame(width: 72, height: 54)
                    .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(image.filename)
                    .font(Tokens.sidebarBody)
                    .foregroundStyle(Tokens.textPrimary)
                    .lineLimit(1)
                Text(ByteCountFormatter.string(fromByteCount: Int64(image.data.count), countStyle: .file))
                    .font(Tokens.timestamp)
                    .foregroundStyle(Tokens.textSecondary)
            }
            .padding(.top, 3)
            Button(action: remove) {
                Image(systemName: "xmark")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(Tokens.textSecondary)
                    .frame(width: 20, height: 20)
                    .background(Circle().fill(.white.opacity(0.06)))
            }
            .buttonStyle(.plain)
            .help("Remove image")
        }
        .padding(6)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(.black.opacity(0.18))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(Tokens.hairline, lineWidth: 1)
        )
        .padding(.leading, 2)
    }
}
