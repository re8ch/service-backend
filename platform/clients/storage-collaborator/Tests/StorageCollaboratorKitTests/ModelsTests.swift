import XCTest
@testable import StorageCollaboratorKit
final class ModelsTests:XCTestCase{func testChangePageDecoding()throws{let d=JSONDecoder();d.dateDecodingStrategy = .iso8601;let p=try d.decode(ChangePage.self,from:Data(#"{"changes":[],"cursor":4,"hasMore":false}"#.utf8));XCTAssertEqual(p.cursor,4)}}
